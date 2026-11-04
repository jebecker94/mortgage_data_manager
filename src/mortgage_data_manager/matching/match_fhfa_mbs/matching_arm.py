"""The FHFA->UMBS ARM crosswalk: FHFA ARM acquisitions against the UMBS ARM disclosures.

The enterprises' single-family loan-level files carry ARMs only in their non-standard
releases, which end in 2020Q3 (Fannie) and 2021Q2 (Freddie), so the SF-keyed crosswalks
(``matching.py``) reach almost no FHFA ARM acquired later. The UMBS disclosures carry those
loans: Fannie's issuance-level ILLD file from 2019-06, and Freddie's monthly FU file,
which has carried ARM PCs since April 2026. This module matches FHFA ARM records against
them and writes one crosswalk for both enterprises, keyed on the UMBS ``Loan Identifier``.
The SF-keyed crosswalks are untouched.

Each round (``ROUNDS_ARM``, one today) is a `~.matching.match_pass` pooled over every
acquisition year, on what earlier rounds left:

* **Keys.** The engine's exact keys less MSA (the disclosures carry no geography below
  the state), plus the origination year. The disclosures date a loan only by its first
  payment, due on the first of the second month after closing, so the loan side's
  origination year is the year of the first payment date less two months.
* **Admissible origination years.** FHFA's note-date flag says whether a record was
  originated in its acquisition year or in an earlier one; `offer_origination_years`
  offers each record to the join once per year the flag admits.
* **Resolution.** The enterprise's own tolerances, score and quality tiers, then mutual
  best and one-to-one with an FHFA record identified by its number *and* year, since
  record numbers repeat across years. Pooling the years lets that one resolution settle
  a loan two acquisition years both claim, with no separate cross-year step.

ARM cells are sparse enough that these keys identify the loan, so neither a fixpoint over
the leftovers (a handful of pairs) nor FHFA's introductory-rate period is used. As a key
the period would cost more true pairs than it saves: FHFA reports some Freddie 60- and
120-month products as 59, 119 or 121.

The pass runs on the full pools, so it also pairs loans the SF-keyed chain reaches
(FHFA -> SF loan -> UMBS loan). On those 27,443 chain-true pairs it agrees 0.99994 of
the time for Fannie and 0.9992 for Freddie, and recovers 99.8% and 99.5% of the chain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.harmonized import (
    FHFA_CHANNEL_CODE,
    FHFA_LOAN_PURPOSE_CODE,
    FHFA_PROPERTY_TYPE_CODE,
    GSE_CHANNEL,
    GSE_LOAN_PURPOSE,
    GSE_OCCUPANCY,
    GSE_PROPERTY_TYPE,
    OCCUPANCY_CODE,
    compose,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.core.reference.states import STATE_TO_FIPS
from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_fhfa_mbs.inputs import FHLMC, FNMA
from mortgage_data_manager.matching.match_fhfa_mbs.matching import (
    admitted,
    load_fhfa,
    match_pass,
    prepare_fhfa,
)
from mortgage_data_manager.matching.match_fhfa_mbs.recovery import tag_round
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import ROUNDS_ARM
from mortgage_data_manager.matching.match_fhfa_mbs.spec import FHFA_ID, LOAN_ID, GseSpec
from mortgage_data_manager.matching.resolve import assert_one_to_one
from mortgage_data_manager.matching.terms import TERM_TO_FHFA_BIN, is_bin_edge, round_to_fhfa_bin
from mortgage_data_manager.sources.umbs.config import UMBSConfig

logger = get_logger(__name__)

#: The UMBS loan key on the written crosswalk.
UMBS_LOAN_ID = "umbs_loan_id"

#: The crosswalk file, one for both enterprises and every acquisition year.
CROSSWALK_NAME = "fhfa_mbs_crosswalk_arm.parquet"

#: FHFA introductory-rate periods that do not mark an ARM: 999 (no introductory rate)
#: and the 12- and 24-month temporary buydowns, fixed-rate loans on the loan-level side.
NON_ARM_INTRO_PERIODS: tuple[int, ...] = (12, 24, 999)

#: How many years before acquisition a prior-year-flagged record may have been originated.
SEASONED_YEARS = 5

#: The first FU month carrying ARM rows; every earlier FU file holds none.
FU_FIRST_ARM_MONTH = "202604"

#: The disclosures' code for a ratio that is not available.
NOT_AVAILABLE = 999

#: An FHFA record in a pass pooled over acquisition years.
RECORD_KEY: tuple[str, ...] = (FHFA_ID, "year")

#: The disclosure columns the preparer reads. Fannie's ILLD and Freddie's FU share them.
UMBS_COLUMNS: tuple[str, ...] = (
    "Loan Identifier",
    "Amortization Type",
    "Property State",
    "Channel",
    "Original Interest Rate",
    "Mortgage Loan Amount",
    "Loan-To-Value (LTV)",
    "Combined Loan-To-Value (CLTV)",
    "Debt-To-Income (DTI)",
    "Loan Term",
    "Number of Units",
    "Number of Borrowers",
    "First Time Home Buyer Indicator",
    "Loan Purpose",
    "Occupancy Status",
    "Property Type",
    "First Payment Date",
)


@dataclass(frozen=True)
class UmbsArmSource:
    """One enterprise's UMBS ARM disclosure and the FHFA conventions it is read under.

    Attributes:
        spec: The enterprise's `~.spec.GseSpec`, for the FHFA enterprise flag, the
            tolerances, the score and the quality tiers: the ARM pass runs each
            enterprise as its SF-keyed crosswalk does.
        files: The disclosure files holding the enterprise's ARM rows, in month order.
        channel_codes: Disclosure channel code to FHFA channel code.
        term_bins: FHFA's re-binning of the enterprise's loan terms, or None where FHFA
            reports them verbatim.
        max_borrowers: FHFA's cap on the enterprise's borrower count, or None.
    """

    spec: GseSpec
    files: Callable[[], list[Path]]
    channel_codes: Mapping[str, int]
    term_bins: Mapping[int, int] | None = None
    max_borrowers: int | None = None


def fnma_arm_files() -> list[Path]:
    """Fannie's ILLD files, each holding the loans in the pools issued that month."""
    return sorted((UMBSConfig.get_prefix_dir("silver", "FNMA") / "ILLD").glob("ILLD_*.parquet"))


def fhlmc_arm_files() -> list[Path]:
    """Freddie's FU files from the first month carrying ARMs.

    FU is a monthly snapshot of outstanding loans, so an ARM that paid off before
    April 2026 is in none of them.

    Returns:
        The FU files from ``FU_FIRST_ARM_MONTH`` on, in month order.
    """
    directory = UMBSConfig.get_prefix_dir("silver", "FHLMC") / "FU"
    return [
        f
        for f in sorted(directory.glob("FU_*.parquet"))
        if f.stem.removeprefix("FU_") >= FU_FIRST_ARM_MONTH
    ]


FNMA_ARM = UmbsArmSource(
    spec=FNMA,
    files=fnma_arm_files,
    # FHFA codes Fannie correspondent loans 9, unlike Freddie's 3.
    channel_codes=compose(GSE_CHANNEL, FHFA_CHANNEL_CODE["FNMA"]),
)

FHLMC_ARM = UmbsArmSource(
    spec=FHLMC,
    files=fhlmc_arm_files,
    # FU's 'T' (third party, unspecified) has no FHFA code and stays null.
    channel_codes=compose(GSE_CHANNEL, FHFA_CHANNEL_CODE["FHLMC"]),
    term_bins=TERM_TO_FHFA_BIN,
    max_borrowers=FHLMC.max_borrowers,
)

UMBS_ARM_SOURCES: tuple[UmbsArmSource, ...] = (FNMA_ARM, FHLMC_ARM)


def load_umbs_arms(source: UmbsArmSource) -> pl.LazyFrame:
    """Scan an enterprise's UMBS ARM rows, one per loan.

    A loan recurs across the monthly files wherever the disclosure restates it (FU
    restates every outstanding loan). Its origination fields are static, so identical
    restatements collapse to one row; a loan whose restatements disagree on any of
    these fields is excluded rather than resolved by picking one disclosure.

    Args:
        source: The enterprise's disclosure.

    Returns:
        The disclosure columns of every ARM row, one row per ``Loan Identifier``.

    Raises:
        FileNotFoundError: If the enterprise has no disclosure files.
    """
    files = source.files()
    if not files:
        raise FileNotFoundError(f"No UMBS ARM disclosure files for {source.spec.name.upper()}")
    scans = [
        pl.scan_parquet(f)
        .select(UMBS_COLUMNS)
        .filter(pl.col("Amortization Type").cast(pl.String) == "ARM")
        for f in files
    ]
    return (
        pl.concat(scans, how="diagonal_relaxed")
        .unique(maintain_order=True)
        .unique(subset="Loan Identifier", keep="none", maintain_order=True)
    )


def prepare_umbs_arms(lf: pl.LazyFrame, source: UmbsArmSource) -> pl.LazyFrame:
    """Map UMBS ARM disclosure rows onto the canonical matching schema.

    The disclosures code an unavailable LTV, CLTV or DTI as 999, nulled here so the
    engine treats it as missing rather than as a deviation. They carry no MSA or ZIP;
    ``zip3`` is emitted null, which the score treats as neutral.

    Args:
        lf: The enterprise's ARM rows (`load_umbs_arms`).
        source: The enterprise's disclosure and conventions.

    Returns:
        The rows on the canonical schema, with ``origination_year`` from the first
        payment date.
    """

    def code(column: str, mapping: Mapping[str, int]) -> pl.Expr:
        return (
            pl.col(column)
            .cast(pl.String)
            .replace_strict(mapping, default=None, return_dtype=pl.Int64)
        )

    def reported(column: str) -> pl.Expr:
        value = pl.col(column).cast(pl.Float64)
        return pl.when(value < NOT_AVAILABLE).then(value)

    term = pl.col("Loan Term").cast(pl.Int64)
    if source.term_bins is not None:
        term = term.replace_strict(source.term_bins, default=term)
    borrowers = pl.col("Number of Borrowers").cast(pl.Int64)
    if source.max_borrowers is not None:
        borrowers = borrowers.clip(upper_bound=source.max_borrowers)

    return lf.select(
        pl.col("Loan Identifier").alias(LOAN_ID),
        pl.col("Property State")
        .replace_strict(STATE_TO_FIPS, default=None, return_dtype=pl.Int64)
        .alias("state_fips"),
        code("Channel", source.channel_codes).alias("channel"),
        pl.col("Original Interest Rate").cast(pl.Float64).alias("interest_rate"),
        pl.col("Mortgage Loan Amount").cast(pl.Float64).alias("loan_amount"),
        # FHFA's "LTV at origination" is CLTV where available, so use CLTV first.
        pl.coalesce(
            reported("Combined Loan-To-Value (CLTV)"), reported("Loan-To-Value (LTV)")
        ).alias("ltv"),
        reported("Debt-To-Income (DTI)").alias("dti"),
        term.alias("term"),
        pl.col("Number of Units").cast(pl.Int64).alias("num_units"),
        borrowers.alias("num_borrowers"),
        code("First Time Home Buyer Indicator", {"Y": 1, "N": 2}).alias("fthb"),
        code("Loan Purpose", compose(GSE_LOAN_PURPOSE, FHFA_LOAN_PURPOSE_CODE)).alias(
            "loan_purpose"
        ),
        code("Occupancy Status", compose(GSE_OCCUPANCY, OCCUPANCY_CODE)).alias("occupancy_code"),
        code("Property Type", compose(GSE_PROPERTY_TYPE, FHFA_PROPERTY_TYPE_CODE)).alias(
            "property_type"
        ),
        pl.col("First Payment Date")
        .dt.offset_by("-2mo")
        .dt.year()
        .cast(pl.Int64)
        .alias("origination_year"),
        pl.lit(None, dtype=pl.String).alias("zip3"),
    ).with_columns(
        round_to_fhfa_bin(pl.col("loan_amount")).alias("loan_amount_rounded"),
        is_bin_edge(pl.col("loan_amount")).alias("_is_bin_edge"),
    )


def offer_origination_years(
    fhfa: pl.LazyFrame, seasoned_years: int = SEASONED_YEARS
) -> pl.LazyFrame:
    """Offer each FHFA record to the join once per origination year its flag admits.

    Flag 1 (originated in the acquisition year) admits that year. Flag 2 (originated
    in an earlier year) admits the ``seasoned_years`` years before acquisition and the
    acquisition year itself: a first payment falling a month later than usual carries
    a late-December note into January on the loan side. A loan has one origination
    year, so it meets a record through at most one of the record's offers.

    Args:
        fhfa: Prepared FHFA records carrying ``year`` and ``orig_year_flag``.
        seasoned_years: How many years before acquisition a flag-2 record reaches.

    Returns:
        One row per record and admissible ``origination_year``; a record with any
        other flag gets a null year, which joins nothing.
    """
    flag = pl.col("orig_year_flag")
    span = pl.when(flag == 1).then(1).when(flag == 2).then(seasoned_years + 1)
    return (
        fhfa.with_columns(pl.int_ranges(0, span).alias("_lag"))
        .explode("_lag")
        .with_columns((pl.col("year").cast(pl.Int64) - pl.col("_lag")).alias("origination_year"))
        .drop("_lag")
    )


def load_fhfa_arms(year: int, spec: GseSpec) -> pl.LazyFrame:
    """FHFA's ARM acquisitions for one year and enterprise, ready for the pooled join.

    Args:
        year: The FHFA acquisition year.
        spec: The enterprise.

    Returns:
        The year's ARM records on the canonical schema, once per admissible
        origination year.
    """
    period = pl.col("introductory_rate_period")
    arms = load_fhfa(year, spec).filter(period.is_not_null() & ~period.is_in(NON_ARM_INTRO_PERIODS))
    return offer_origination_years(prepare_fhfa(arms, spec))


def match_umbs_arms(source: UmbsArmSource, years: Iterable[int]) -> pl.DataFrame:
    """Match one enterprise's FHFA ARM records, pooled over ``years``, to its UMBS ARMs.

    Args:
        source: The enterprise's disclosure and conventions.
        years: FHFA acquisition years to pool; a year without an FHFA file is skipped.

    Returns:
        The matched pairs, one-to-one, with every matched column and ``match_round``
        set to each round's position in `ROUNDS_ARM`.

    Raises:
        FileNotFoundError: If no requested year has an FHFA file.
    """
    spec = source.spec
    fhfa: list[pl.LazyFrame] = []
    for year in years:
        try:
            fhfa.append(load_fhfa_arms(year, spec))
        except FileNotFoundError as e:
            logger.warning("Skipping year %d: %s", year, e)
    if not fhfa:
        raise FileNotFoundError(f"No FHFA file for any requested year ({spec.name.upper()})")

    umbs = prepare_umbs_arms(load_umbs_arms(source), source).collect()
    records = pl.concat(fhfa, how="diagonal_relaxed").collect()
    rounds: list[pl.DataFrame] = []
    for number, rnd in enumerate(ROUNDS_ARM, start=1):
        found = match_pass(
            umbs.lazy(), records.lazy(), spec, keys=rnd.keys, record_key=RECORD_KEY
        ).collect()
        logger.info("  round %d (%s): %s pairs", number, rnd.description, f"{len(found):,}")
        rounds.append(tag_round(found, number))
        umbs = umbs.join(found.select(LOAN_ID), on=LOAN_ID, how="anti")
        records = records.join(found.select(RECORD_KEY), on=list(RECORD_KEY), how="anti")
    pairs = pl.concat(rounds, how="diagonal_relaxed")
    logger.info("%s: %s FHFA->UMBS ARM pairs", spec.name.upper(), f"{len(pairs):,}")
    for row in pairs.group_by("year").agg(pl.len()).sort("year").iter_rows(named=True):
        logger.info("  %d: %s", row["year"], f"{row['len']:,}")
    return pairs


def crosswalk(pairs: pl.DataFrame) -> pl.DataFrame:
    """Cut the crosswalk columns from matched pairs, keeping the evidence-decided ones.

    The pass runs each enterprise's own score, so Freddie's carries the amount term
    here too; `~.matching.admitted` drops the pairs that term settled rather than the
    evidence-bearing fields.

    Args:
        pairs: Pairs from `match_umbs_arms`.

    Returns:
        The crosswalk columns.
    """
    return admitted(pairs).select(
        pl.col(LOAN_ID).alias(UMBS_LOAN_ID),
        FHFA_ID,
        pl.col("year").alias("fhfa_year"),
        pl.col("fhfa_enterprise").cast(pl.Int8),
        "match_round",
        pl.col("match_quality_tier").cast(pl.UInt8),
    )


def run_matching_arm(
    min_year: int = 2018,
    max_year: int = 2025,
    output_dir: Path = Config.FHFA_MBS_CROSSWALK_DIR,
) -> pl.DataFrame:
    """Match both enterprises' FHFA ARM records to the UMBS ARM disclosures and write the crosswalk.

    Args:
        min_year: First FHFA acquisition year (inclusive).
        max_year: Last FHFA acquisition year (inclusive).
        output_dir: Directory the crosswalk is written to, as ``CROSSWALK_NAME``.

    Returns:
        The crosswalk as written.
    """
    years = range(min_year, max_year + 1)
    parts = [crosswalk(match_umbs_arms(source, years)) for source in UMBS_ARM_SOURCES]
    # FHFA numbers each enterprise's records separately, and the two enterprises'
    # UMBS ids share a 10-digit form, so both keys carry the enterprise.
    result = assert_one_to_one(
        pl.concat(parts),
        ["fhfa_enterprise", UMBS_LOAN_ID],
        ["fhfa_enterprise", FHFA_ID, "fhfa_year"],
        context="FHFA->UMBS ARM crosswalk",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / CROSSWALK_NAME
    with atomic_output(path) as tmp:
        result.write_parquet(tmp)
    logger.info("Crosswalk saved to: %s (%s pairs)", path, f"{len(result):,}")
    return result
