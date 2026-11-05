"""Post-2018 HMDA-FHA matching: prepare both sides, then run the rounds in ``rounds.py``.

Preparation rounds the FHA rate to 12.5bp and amount to $10k and attaches ZIP to HMDA
through the census tract. The engine runs `ROUNDS_POST2018` in order, each on what earlier
rounds left unmatched, and writes one crosswalk tagged with ``match_round``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference import to_county_vintage
from mortgage_data_manager.core.reference.zip_tract import (
    build_zip_tract,
    tract_vintage,
    zip_tract_file,
)
from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_fha.rounds import ROUNDS_POST2018, Round
from mortgage_data_manager.matching.names import jaccard, name_tokens
from mortgage_data_manager.matching.resolve import drop_ambiguous, unique_best
from mortgage_data_manager.sources.hmda.load import (
    null_sentinel_codes,
    scan_hmda_for_index,
    scan_hmda_years,
    scan_originations,
)
from mortgage_data_manager.sources.usps_crosswalk.config import CensusVintage

logger = get_logger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def build_zip_tract_crosswalk(
    usps_dir: Path,
    census_vintage: CensusVintage,
    output_path: Path,
) -> Path:
    """Build consolidated ZIP-tract crosswalk from HUD data (`core.reference.zip_tract`).

    Args:
        usps_dir: Path to USPS crosswalk bronze directory.
        census_vintage: Census tract vintage (2000, 2010 or 2020)
        output_path: Path to save consolidated crosswalk

    Returns:
        Path to the saved crosswalk file
    """
    return build_zip_tract(census_vintage, output_path, usps_dir)


def hmda_tract_vintage(year: int) -> CensusVintage:
    """The census whose tracts HMDA reports for an activity year (`tract_vintage`).

    HMDA moved to 2010-census tracts in 2012 and to 2020-census tracts in 2022,
    each a year before HUD's ZIP-tract quarters did.

    Args:
        year: HMDA activity year.

    Returns:
        2000 through 2011, 2010 for 2012-2021, 2020 from 2022.
    """
    return tract_vintage(year)


def get_zip_tract_for_year(
    year: int,
    crosswalk_dir: Path | None = None,
    usps_dir: Path | None = None,
) -> Path:
    """Return the ZIP-tract crosswalk for HMDA's tracts in an activity year.

    The file is the one built from HUD's quarters for the census HMDA reports
    tracts on that year (`hmda_tract_vintage`), and is built if missing.

    Args:
        year: HMDA activity year (determines census vintage)
        crosswalk_dir: Directory for crosswalk files. Defaults to config.
        usps_dir: USPS crosswalk bronze directory. Defaults to config.

    Returns:
        Path to appropriate crosswalk file
    """
    return zip_tract_file(
        tract_vintage(year),
        cache_dir=crosswalk_dir or Config.HMDA_FHA_ZIP_TRACT_DIR,
        data_dir=usps_dir or Config.USPS_CROSSWALK_DIR,
    )


# =============================================================================
# Data Preparation Functions
# =============================================================================


def prepare_fha(
    fha_dir: Path,
    intermediate_dir: Path,
    min_year: int = Config.MIN_YEAR,
    max_year: int = Config.MAX_YEAR,
    file_suffix: str = "_post2018",
) -> Path:
    """Prepare FHA data for merge with HMDA.

    Args:
        fha_dir: Directory where FHA silver data is stored (hive-partitioned)
        intermediate_dir: Directory where intermediate match data will be saved
        min_year: Minimum year to include
        max_year: Maximum year to include
        file_suffix: Suffix for output file names

    Returns:
        Path to saved FHA match data file
    """
    logger.info("Preparing FHA data for matching (years %s-%s)", min_year, max_year)

    df = pl.scan_parquet(fha_dir / "**/*.parquet")

    # Filter to post-2018 period (include Dec of prior year, Jan-Feb of next year)
    df = df.filter(
        pl.col("Year") >= min_year - 1,
        ((pl.col("Year") >= min_year) | (pl.col("Month") >= 11)),
    )
    df = df.filter(pl.col("Year") <= max_year + 1)

    # Create Match Variables
    df = df.with_columns(
        [
            (pl.col("Product Type") == "Adjustable Rate").cast(pl.Int8).alias("i_ARM"),
            (pl.col("Interest Rate") * 1000).round(0).cast(pl.Int16).alias("Interest Rate 1000"),
            ((pl.col("Interest Rate") * 8).round(0) * 125)
            .cast(pl.Int16)
            .alias("Interest Rate 125"),
            ((((pl.col("Mortgage Amount") - 5000) / 10000).round(0) * 10000) + 5000)
            .cast(pl.Int32)
            .alias("Loan Amount Round"),
        ]
    )

    # Drop columns not needed for matching
    drop_cols = [
        "Property City",
        "Property County",
        "Down Payment Source",
        "Property Type",
        "Non Profit Number",
        "Originating Mortgagee",
        "Sponsor Name",
    ]
    df = df.drop(drop_cols, strict=False)

    df = df.sort(
        [
            "Year",
            "Month",
            "Property State",
            "Property Zip",
            "Interest Rate",
            "Mortgage Amount",
        ]
    )

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    save_file = intermediate_dir / f"fha_match_data{file_suffix}.parquet"
    df.sink_parquet(save_file)
    logger.info("Saved FHA match data to %s", save_file)

    return save_file


def prepare_hmda(
    hmda_dir: Path,
    intermediate_dir: Path,
    min_year: int = Config.MIN_YEAR,
    max_year: int = Config.MAX_YEAR,
    file_suffix: str = "_post2018",
    crosswalk_dir: Path | None = None,
    usps_dir: Path | None = None,
) -> Path:
    """Prepare HMDA data for merge with FHA.

    Args:
        hmda_dir: Directory where HMDA silver data is stored (hive-partitioned)
        intermediate_dir: Directory where intermediate match data will be saved
        min_year: Minimum year to include
        max_year: Maximum year to include
        file_suffix: Suffix for output file names
        crosswalk_dir: Directory for crosswalk files
        usps_dir: HUD crosswalk source directory

    Returns:
        Path to saved HMDA match data file
    """
    logger.info("Preparing HMDA data for matching (years %s-%s)", min_year, max_year)

    if crosswalk_dir is None:
        crosswalk_dir = Config.HMDA_FHA_ZIP_TRACT_DIR
    if usps_dir is None:
        usps_dir = Config.USPS_CROSSWALK_DIR

    keep_cols = [
        "activity_year",
        "lei",
        "state_code",
        "county_code",
        "census_tract",
        "action_taken",
        "loan_type",
        "loan_purpose",
        "reverse_mortgage",
        "loan_amount",
        "interest_rate",
        "loan_term",
        "intro_rate_period",
        "total_units",
        "submission_of_application",
        "HMDAIndex",
        "respondent_id",
        "agency_code",
        "sequence_number",
    ]

    df_list: list[pl.LazyFrame] = []
    for year in range(min_year, max_year + 1):
        try:
            lf = scan_originations("post2018", year, year, loan_types=[2], root=hmda_dir)
        except FileNotFoundError as e:
            logger.warning("Skipping year %s: %s", year, e)
            continue
        logger.info("Reading HMDA %s", year)

        use_cols = [c for c in lf.collect_schema().names() if c in keep_cols]
        lf = lf.select(use_cols)

        if "reverse_mortgage" in use_cols:
            lf = lf.filter(pl.col("reverse_mortgage") != 1)
        if "total_units" in use_cols:
            lf = lf.filter(pl.col("total_units") <= 4)

        drop_cols = ["action_taken", "loan_type", "reverse_mortgage", "total_units"]
        cols_to_drop = [c for c in drop_cols if c in use_cols]
        if cols_to_drop:
            lf = lf.drop(cols_to_drop)

        df_list.append(lf)

    if not df_list:
        raise ValueError(f"No HMDA data found for years {min_year}-{max_year}")

    # diagonal: align by name. HMDA file_type vintages order the same columns
    # differently (e.g. the 2025 MLAR 'e' file), which a vertical concat rejects.
    df = pl.concat(df_list, how="diagonal").collect()
    logger.info("Combined HMDA data: %s rows", len(df))

    # CT planning-region bridge: FHA endorsement data stays on the traditional CT
    # counties (09001-09015) while HMDA switched its tract/county coding to
    # planning regions (09110-09190) in 2024. Without this, the downstream
    # FIPS == county_code geography filter drops every CT 2024+ FHA<->HMDA pair.
    # Translate HMDA's planning-region tracts back to the 2020 county convention
    # and refresh county_code from the (now county-coded) tract for CT rows. The
    # ZIP<->tract crosswalk carries both conventions, so the ZIP join is
    # unaffected. Idempotent for pre-2024 / non-CT records.
    df = to_county_vintage(
        df.lazy(),
        tract_col="census_tract",
        county_col="county_code" if "county_code" in df.columns else None,
    ).collect()

    columns = df.columns

    # Replace exemption codes with null
    df = null_sentinel_codes(df, "post2018", ["interest_rate", "submission_of_application"])

    # Create ARM indicator (validation reporting only; see _apply_common_filters)
    if "intro_rate_period" in columns and "loan_term" in columns:
        df = df.with_columns(
            pl.when((pl.col("intro_rate_period") == -99999) | (pl.col("loan_term") == -99999))
            .then(None)
            .when(
                (pl.col("intro_rate_period") < pl.col("loan_term"))
                & (pl.col("intro_rate_period") >= 0)
            )
            .then(1)
            .otherwise(0)
            .cast(pl.Int8)
            .alias("1(ARM)")
        ).drop(["loan_term", "intro_rate_period"])

    # Create rounded interest rate columns
    if "interest_rate" in columns:
        df = df.with_columns(
            (pl.col("interest_rate") * 1000)
            .round(0)
            .cast(pl.Int32)
            .alias("HMDA Interest Rate (Integer)")
        )
        df = df.with_columns(
            ((pl.col("HMDA Interest Rate (Integer)") / 125).round(0) * 125)
            .cast(pl.Int32)
            .alias("HMDA Interest Rate (Nearest 125)")
        )

    # Fan each loan to ZIP through the crosswalk of the census its year's tracts come from
    years_by_vintage: dict[CensusVintage, list[int]] = {}
    for year in df["activity_year"].unique().to_list():
        years_by_vintage.setdefault(hmda_tract_vintage(year), []).append(year)
    df = pl.concat(
        [
            df.filter(pl.col("activity_year").is_in(years))
            .join(
                pl.read_parquet(get_zip_tract_for_year(years[0], crosswalk_dir, usps_dir)),
                left_on="census_tract",
                right_on="TRACT",
                how="left",
            )
            .drop(["census_tract"], strict=False)
            for _, years in sorted(years_by_vintage.items())
        ],
        how="diagonal",
    )

    # Ensure string columns
    string_columns = ["respondent_id", "state_code", "HMDAIndex", "lei"]
    cast_exprs = [pl.col(col).cast(pl.Utf8) for col in string_columns if col in df.columns]
    if cast_exprs:
        df = df.with_columns(cast_exprs)

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    save_file = intermediate_dir / f"hmda_match_data{file_suffix}.parquet"
    df.write_parquet(save_file)
    logger.info("Saved HMDA match data to %s", save_file)

    return save_file


# =============================================================================
# Gates
# =============================================================================


def _purpose_consistent() -> pl.Expr:
    """An HMDA purchase never pairs with an FHA refinance, nor an HMDA refinance with a purchase."""
    return ~(
        (pl.col("loan_purpose") == 1) & pl.col("Loan Purpose").is_in(["Refi_FHA", "Refi_Conv_Curr"])
    ) & ~(pl.col("loan_purpose").is_in([31, 32]) & (pl.col("Loan Purpose") == "Purchase"))


def _sponsor_consistent() -> pl.Expr:
    """A third-party HMDA submission has an FHA sponsor; a direct one has none."""
    return ~((pl.col("submission_of_application") == 2) & pl.col("Sponsor Number").is_null()) & ~(
        (pl.col("submission_of_application") == 1) & pl.col("Sponsor Number").is_not_null()
    )


def _apply_gates(df: pl.DataFrame, round_: Round) -> pl.DataFrame:
    """Drop candidates that fail the purpose gate, the round's rate gate or its sponsor gate.

    Fixed-versus-adjustable agreement between the two sides is deliberately not a gate.
    HMDA ``intro_rate_period`` carries temporary buydown periods for a subset of filers and
    FHA ``Product Type`` miscodes some genuine ARMs, so the two flags disagree on about
    0.15% of pairs that are otherwise validated; 99.5% of the adjudicated disagreements
    agree with the GNMA loan on held-out term, units and rate type. Enforcing agreement
    removes correct matches and catches almost none that are wrong.

    Args:
        df: Candidate pairs carrying ``loan_purpose``, ``Loan Purpose``,
            ``interest_rate``, ``Interest Rate``, ``submission_of_application`` and
            ``Sponsor Number``.
        round_: The round whose ``rate_tolerance`` and ``sponsor_filter`` apply.

    Returns:
        The candidates that pass.
    """
    df = df.filter(
        _purpose_consistent(),
        (pl.col("interest_rate") - pl.col("Interest Rate")).abs() <= round_.rate_tolerance,
    )
    if round_.sponsor_filter:
        df = df.filter(_sponsor_consistent())
    return df


#: Per-(Master Number, lei) statistics the lender filter computes, in the order written.
_LENDER_STAT_COLUMNS = [
    "Count Mortgagee",
    "Count LEI",
    "Count Matches (FHA)",
    "Count Matches (HMDA)",
    "Fraction Matches (FHA)",
    "Fraction Matches (HMDA)",
]


def _with_lender_match_stats(df: pl.DataFrame) -> pl.DataFrame:
    """Attach each candidate's lender-pair statistics.

    Counts are distinct loans: per mortgagee (``Master Number``), per ``lei``, and per
    (mortgagee, lei) pair from each side; the fractions are the pair's share of the
    mortgagee's FHA loans and of the LEI's HMDA loans.

    Args:
        df: Candidate pairs carrying ``Master Number``, ``lei``, ``FHA_Index`` and
            ``HMDAIndex``.

    Returns:
        ``df`` with the `_LENDER_STAT_COLUMNS` added.
    """
    df = df.with_columns(
        [
            pl.col("FHA_Index").n_unique().over("Master Number").alias("Count Mortgagee"),
            pl.col("HMDAIndex").n_unique().over("lei").alias("Count LEI"),
            pl.col("FHA_Index")
            .n_unique()
            .over(["Master Number", "lei"])
            .alias("Count Matches (FHA)"),
            pl.col("HMDAIndex")
            .n_unique()
            .over(["Master Number", "lei"])
            .alias("Count Matches (HMDA)"),
        ]
    )
    return df.with_columns(
        [
            (pl.col("Count Matches (FHA)") / pl.col("Count Mortgagee")).alias(
                "Fraction Matches (FHA)"
            ),
            (pl.col("Count Matches (HMDA)") / pl.col("Count LEI")).alias("Fraction Matches (HMDA)"),
        ]
    )


def _ranked_lender_pairs(df: pl.DataFrame) -> pl.DataFrame:
    """One row per (Master Number, lei) pair with its statistics, ``Rank`` and ``Max Fraction``.

    ``Rank`` is the pair's dense rank by FHA match count within its mortgagee (1 = the
    mortgagee's most-matched LEI, ties sharing it); ``Max Fraction`` is the mortgagee's
    largest FHA match fraction.

    Args:
        df: Candidate pairs carrying the `_LENDER_STAT_COLUMNS`.

    Returns:
        The distinct lender pairs, ranked.
    """
    return (
        df.select(["Master Number", "lei", *_LENDER_STAT_COLUMNS])
        .unique()
        .with_columns(
            [
                pl.col("Count Matches (FHA)")
                .rank("dense", descending=True)
                .over("Master Number")
                .alias("Rank"),
                pl.col("Fraction Matches (FHA)").max().over("Master Number").alias("Max Fraction"),
            ]
        )
    )


def _lender_pair_kept() -> pl.Expr:
    """Keep a lender pair that is its mortgagee's top rank, small, or a minor share."""
    return (
        (pl.col("Rank") == 1)
        | (pl.col("Count Mortgagee") < 1000)
        | (pl.col("Fraction Matches (FHA)") < 1 - pl.col("Max Fraction"))
    )


def _apply_lender_filter(df: pl.DataFrame) -> pl.DataFrame:
    """Apply lender quality filtering (two rounds of filtering + shared match check).

    This is the lender filtering logic from Round 1. Candidate pairs it removes agree with
    the corresponding securitized loan on held-out term, units, borrowers and CLTV at close
    to the rate a deliberately mismatched control does, so the gate removes coincidental
    same-cell twins rather than true pairs.

    There is no minimum lender size: dropping lenders with few surviving matches removes
    already-1:1 pairs whose held-out agreement matches the on-diagonal rate, i.e. correct
    matches, and releasing a record lets a later round assign it to a different
    counterparty.

    Args:
        df: DataFrame with candidate matches. Must contain columns: 'Master Number'
            (created from Originating Mortgagee Number or Sponsor Number), 'lei',
            'FHA_Index', and 'HMDAIndex'.

    Returns:
        Filtered DataFrame retaining only high-quality lender-to-LEI matches based on
        match frequency and fraction thresholds (>= 1% of loans for each entity, top-ranked
        matches, and shared match analysis)
    """
    # === Lender Filtering Round 1 ===
    df = _with_lender_match_stats(df)
    df = df.filter(pl.col("Fraction Matches (FHA)") >= 0.01)
    df = df.filter(pl.col("Fraction Matches (HMDA)") >= 0.01)

    # Keep Good Lender Matches (Round 1)
    lender_matches = _ranked_lender_pairs(df).filter(_lender_pair_kept())
    df = df.join(
        lender_matches.select(["Master Number", "lei"]),
        on=["Master Number", "lei"],
        how="inner",
    )

    # === Lender Filtering Round 2 === (statistics recomputed on the round-1 survivors)
    df = _with_lender_match_stats(df.drop(_LENDER_STAT_COLUMNS))
    lender_matches = _ranked_lender_pairs(df)
    lender_matches = lender_matches.filter(pl.col("Max Fraction") >= 0.5)
    lender_matches = lender_matches.filter(_lender_pair_kept())
    df = df.join(
        lender_matches.select(["Master Number", "lei", "Rank"]),
        on=["Master Number", "lei"],
        how="inner",
    )

    # === Matched to Best ===
    df = df.with_columns(pl.col("Rank").min().over("FHA_Index").alias("Min Rank"))
    df = df.with_columns((pl.col("Min Rank") == 1).alias("1(Matched to Best)"))
    df = df.with_columns(
        pl.when(pl.col("1(Matched to Best)"))
        .then(pl.col("FHA_Index").n_unique().over(["Master Number", "lei", "1(Matched to Best)"]))
        .otherwise(None)
        .alias("Count Shared Matches")
    )
    df = df.with_columns(
        pl.col("Count Shared Matches")
        .max()
        .over(["Master Number", "lei"])
        .alias("Count Shared Matches")
    )
    df = df.with_columns(
        (pl.col("Count Shared Matches") / pl.col("Count Matches (FHA)")).alias(
            "Fraction Shared Matches"
        )
    )
    df = df.filter(~((pl.col("Rank") != 1) & (pl.col("Fraction Shared Matches") > 0.1)))

    # Drop intermediate columns
    df = df.drop(
        [
            *_LENDER_STAT_COLUMNS,
            "Rank",
            "Min Rank",
            "1(Matched to Best)",
            "Count Shared Matches",
            "Fraction Shared Matches",
        ]
    )

    return df


# =============================================================================
# Rounds
# =============================================================================

#: HMDA purchaser types whose loan stays with its originator (2 Ginnie Mae, 0 not sold), so
#: an LEI that does not align with the FHA mortgagee counts against the pair.
EXPECT_ALIGN_PTS: tuple[int, ...] = (2, 0)

#: Score points each geography tier earns in a scored round.
_GEO_POINTS: dict[str, int] = {"zip": 1, "county_no_zip": 0, "county_fallback": 0}

#: HMDA join-key name -> its FHA counterpart.
_FHA_KEY: dict[str, str] = {
    "state_code": "Property State",
    "loan_amount": "Loan Amount Round",
    "HMDA Interest Rate (Nearest 125)": "Interest Rate 125",
    "ZIP": "Property Zip",
    "county_code": "FIPS",
}
_BASE_KEYS = ["state_code", "loan_amount", "HMDA Interest Rate (Nearest 125)"]


def _county_agrees() -> pl.Expr:
    """FHA county equals HMDA county, or either is unknown."""
    return (
        (pl.col("FIPS") == pl.col("county_code"))
        | pl.col("FIPS").is_null()
        | pl.col("county_code").is_null()
    )


def _join(hmda: pl.DataFrame, fha: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """Inner join on HMDA key names and their FHA counterparts."""
    return hmda.join(fha, left_on=keys, right_on=[_FHA_KEY[k] for k in keys], how="inner")


def _candidates(round_: Round, hmda: pl.DataFrame, fha: pl.DataFrame) -> pl.DataFrame:
    """The round's candidate pairs from every geography tier, one activity year at a time.

    Each activity year meets only the FHA records endorsed inside the round's window
    around it. The prepared HMDA frame holds one row per (loan, candidate ZIP); a loan's
    rows differ only in ZIP, and an FHA record has one ZIP, so the ``zip`` tier yields each
    pair once. ``county_fallback`` drops the ZIP and collapses those identical rows.

    Args:
        round_: The round whose ``geo``, ``county_check`` and ``window`` apply.
        hmda: Unclaimed prepared HMDA rows.
        fha: Unclaimed prepared FHA records, with ``endorse_idx``.

    Returns:
        Candidate pairs carrying both sides' columns and the tier's ``s_geo`` points.
    """
    has_rate = pl.col("interest_rate").is_not_null()
    has_zip = pl.col("ZIP").is_not_null()
    lo, hi = round_.window
    frames: list[pl.DataFrame] = []
    for year in sorted(hmda["activity_year"].unique().to_list()):
        hmda_y = hmda.filter(pl.col("activity_year") == year)
        fha_y = fha.filter(pl.col("endorse_idx").is_between(year * 12 + lo, year * 12 + hi))
        zip_loans: pl.DataFrame | None = None
        for tier in round_.geo:
            if tier == "zip":
                pairs = _join(hmda_y.filter(has_zip & has_rate), fha_y, [*_BASE_KEYS, "ZIP"])
                if round_.county_check:
                    pairs = pairs.filter(_county_agrees())
                zip_loans = pairs.select("HMDAIndex").unique()
            elif tier == "county_no_zip":
                pairs = _join(hmda_y.filter(~has_zip & has_rate), fha_y, _BASE_KEYS).filter(
                    _county_agrees()
                )
            elif tier == "county_fallback":
                if zip_loans is None:
                    raise ValueError("geo tier 'county_fallback' must follow 'zip'")
                loans = (
                    hmda_y.filter(has_rate & pl.col("county_code").is_not_null())
                    .join(zip_loans, on="HMDAIndex", how="anti")
                    .drop("ZIP")
                    .unique()
                )
                pairs = _join(
                    loans, fha_y.filter(pl.col("FIPS").is_not_null()), [*_BASE_KEYS, "county_code"]
                )
            else:
                raise ValueError(f"unknown geo tier: {tier!r}")
            frames.append(pairs.with_columns(pl.lit(_GEO_POINTS[tier], pl.Int8).alias("s_geo")))
    return pl.concat(frames, how="diagonal_relaxed")


def _score(pairs: pl.DataFrame, roster: pl.DataFrame) -> pl.DataFrame:
    """Score candidates for `unique_best` (`Round.min_score` describes the points).

    A pair is identity-aligned when the HMDA panel name and the FHA originating-mortgagee
    or sponsor name share at least half their tokens (Jaccard), or when the (LEI, mortgagee)
    or (LEI, sponsor) pair is on the roster earlier rounds learned. ``rank_val`` orders by
    score, then by endorsement-timing distance from mid activity year.

    Args:
        pairs: Gated candidates carrying ``lei``, ``lei_name``, ``orig_num``,
            ``orig_name``, ``sponsor_num``, ``spon_name``, ``purchaser_type`` and
            ``s_geo``.
        roster: (``lei``, ``orig_num``, ``sponsor_num``) rows from earlier rounds.

    Returns:
        ``pairs`` with the score components, ``score`` and ``rank_val``.
    """
    orig = roster.filter(pl.col("orig_num").fill_null("") != "").select("lei", "orig_num").unique()
    spon = (
        roster.filter(pl.col("sponsor_num").fill_null("") != "")
        .select("lei", "sponsor_num")
        .unique()
    )
    pairs = pairs.join(
        orig.with_columns(pl.lit(True).alias("orig_roster_hit")), on=["lei", "orig_num"], how="left"
    ).join(
        spon.with_columns(pl.lit(True).alias("spon_roster_hit")),
        on=["lei", "sponsor_num"],
        how="left",
    )
    # fill_nan as well as fill_null: two empty names give an empty token union, so the
    # Jaccard is 0/0 = NaN, and polars orders NaN above every number (`NaN >= 0.5`).
    pairs = pairs.with_columns(
        jo=jaccard(name_tokens("lei_name"), name_tokens("orig_name")).fill_nan(0.0).fill_null(0.0),
        js=jaccard(name_tokens("lei_name"), name_tokens("spon_name")).fill_nan(0.0).fill_null(0.0),
    ).with_columns(
        (
            (pl.col("jo") >= 0.5)
            | (pl.col("js") >= 0.5)
            | pl.col("orig_roster_hit").fill_null(False)
            | pl.col("spon_roster_hit").fill_null(False)
        ).alias("aligned")
    )
    pairs = pairs.with_columns(
        pl.when(pl.col("aligned"))
        .then(2)
        .when(pl.col("purchaser_type").is_in(list(EXPECT_ALIGN_PTS)))
        .then(-2)
        .otherwise(0)
        .alias("s_identity"),
        _sponsor_consistent().cast(pl.Int8).alias("s_sponsor"),
        ((pl.col("interest_rate") - pl.col("Interest Rate")).abs() <= 0.005)
        .cast(pl.Int8)
        .alias("s_rate_exact"),
        (pl.col("Year") + (pl.col("Month") - 6.5) / 12 - pl.col("activity_year"))
        .abs()
        .alias("tdist"),
    )
    return pairs.with_columns(
        (
            pl.col("s_identity") + pl.col("s_sponsor") + pl.col("s_rate_exact") + pl.col("s_geo")
        ).alias("score")
    ).with_columns((pl.col("score") * 10 - pl.col("tdist")).alias("rank_val"))


def _run_round(
    round_: Round, hmda: pl.DataFrame, fha: pl.DataFrame, roster: pl.DataFrame
) -> pl.DataFrame:
    """One round on the unclaimed sides: candidates, gates, then resolution.

    Args:
        round_: The round to run.
        hmda: Unclaimed prepared HMDA rows.
        fha: Unclaimed prepared FHA records.
        roster: The lender roster earlier rounds learned (read by a scored round).

    Returns:
        The round's 1:1 pairs, with both sides' columns.

    Raises:
        ValueError: If a scored round's candidates hold one pair twice.
    """
    candidates = _apply_gates(_candidates(round_, hmda, fha), round_)
    if round_.lender_filter:
        candidates = _apply_lender_filter(
            candidates.with_columns(
                pl.coalesce("Originating Mortgagee Number", "Sponsor Number").alias("Master Number")
            )
        )
    if round_.min_score is None:
        return drop_ambiguous(candidates, "FHA_Index", "HMDAIndex")
    if not candidates.select("HMDAIndex", "FHA_Index").is_unique().all():
        raise ValueError(f"round {round_.description!r}: a candidate pair joined twice")
    scored = _score(candidates, roster).filter(pl.col("score") >= round_.min_score)
    return unique_best(scored, "rank_val", "HMDAIndex", "FHA_Index", descending=True)


def _load_inputs(
    intermediate_dir: Path,
    fha_dir: Path,
    hmda_dir: Path,
    min_year: int,
    max_year: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The prepared HMDA and FHA frames, with what the rounds read beyond the join keys.

    FHA gains its endorsement month index and trimmed mortgagee/sponsor numbers; when a
    round is scored, HMDA gains ``purchaser_type`` and the LEI's panel name and FHA the
    originating-mortgagee and sponsor names. An LEI filing two names in its latest panel
    year has no single name, so it gets none.

    Args:
        intermediate_dir: Directory holding the prepared intermediates.
        fha_dir: FHA silver directory (mortgagee and sponsor names).
        hmda_dir: HMDA silver directory (purchaser type).
        min_year: First HMDA activity year.
        max_year: Last HMDA activity year.

    Returns:
        The HMDA and FHA frames.
    """
    hmda = (
        pl.scan_parquet(intermediate_dir / "hmda_match_data_post2018.parquet")
        .filter(pl.col("activity_year").is_between(min_year, max_year))
        .with_columns(pl.col("ZIP").cast(pl.Utf8).str.zfill(5))
        .collect()
    )
    fha = (
        pl.scan_parquet(intermediate_dir / "fha_match_data_post2018.parquet")
        .filter(pl.col("Year").is_between(min_year - 1, max_year + 1))
        .with_columns(
            pl.col("Property Zip").cast(pl.Utf8).str.zfill(5),
            pl.col("Originating Mortgagee Number")
            .cast(pl.Utf8)
            .str.strip_chars()
            .alias("orig_num"),
            pl.col("Sponsor Number").cast(pl.Utf8).str.strip_chars().alias("sponsor_num"),
            (pl.col("Year").cast(pl.Int32) * 12 + pl.col("Month").cast(pl.Int32) - 1).alias(
                "endorse_idx"
            ),
        )
        .collect()
    )
    if all(r.min_score is None for r in ROUNDS_POST2018):
        return hmda, fha

    purchaser = (
        scan_hmda_for_index(hmda, "post2018", root=hmda_dir)
        .filter((pl.col("loan_type") == 2) & (pl.col("action_taken") == 1))
        .select("HMDAIndex", "purchaser_type")
        .collect(engine="streaming")
    )
    panel = (
        scan_hmda_years("post2018", dataset="panel")
        .select("lei", "respondent_name", "activity_year")
        .filter(pl.col("lei").is_not_null() & (pl.col("lei") != ""))
        .filter(pl.col("activity_year") == pl.col("activity_year").max().over("lei"))
        .select("lei", pl.col("respondent_name").alias("lei_name"))
        .unique()
        .filter(pl.len().over("lei") == 1)
        .collect()
    )
    names = (
        pl.scan_parquet(f"{fha_dir}/**/*.parquet")
        .select(
            "FHA_Index",
            pl.col("Originating Mortgagee").alias("orig_name"),
            pl.col("Sponsor Name").alias("spon_name"),
        )
        .collect(engine="streaming")
    )
    hmda = hmda.join(purchaser, on="HMDAIndex", how="left").join(panel, on="lei", how="left")
    fha = fha.join(names, on="FHA_Index", how="left")
    return hmda, fha


# =============================================================================
# Main Pipeline Function
# =============================================================================


@log_execution_time
def run_matching(
    min_year: int = Config.MIN_YEAR,
    max_year: int = Config.MAX_YEAR,
    fha_dir: Path | None = None,
    hmda_dir: Path | None = None,
    intermediate_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Run the post-2018 HMDA-FHA matcher: prepare both sides, then `ROUNDS_POST2018`.

    Each round sees only what earlier rounds left unmatched, and the crosswalk tags each
    pair with its round's 1-based position as ``match_round``.

    Args:
        min_year: First HMDA activity year to match.
        max_year: Last HMDA activity year to match.
        fha_dir: FHA silver data directory.
        hmda_dir: HMDA silver data directory.
        intermediate_dir: Intermediate match data directory.
        output_dir: Directory the crosswalk is written to.
        overwrite: Rebuild the prepared FHA/HMDA intermediates even if they exist.

    Returns:
        Path to the crosswalk.

    Raises:
        ValueError: If the rounds' union is not 1:1 on both indices.
    """
    logger.info("Starting HMDA-FHA matching pipeline (years %s-%s)", min_year, max_year)

    if fha_dir is None:
        fha_dir = Config.FHA_SILVER_DIR
    if hmda_dir is None:
        hmda_dir = Config.HMDA_SILVER_DIR
    if intermediate_dir is None:
        intermediate_dir = Config.HMDA_FHA_INTERMEDIATE_DIR
    if output_dir is None:
        output_dir = Config.HMDA_FHA_CROSSWALK_DIR

    Config.ensure_directories()

    file_suffix = "_post2018"
    fha_match_file = intermediate_dir / f"fha_match_data{file_suffix}.parquet"
    if should_process_file(fha_match_file, overwrite):
        prepare_fha(fha_dir, intermediate_dir, min_year, max_year, file_suffix)
    else:
        logger.info("Skipping FHA prep - file exists: %s", fha_match_file)
    hmda_match_file = intermediate_dir / f"hmda_match_data{file_suffix}.parquet"
    if should_process_file(hmda_match_file, overwrite):
        prepare_hmda(hmda_dir, intermediate_dir, min_year, max_year, file_suffix)
    else:
        logger.info("Skipping HMDA prep - file exists: %s", hmda_match_file)

    hmda, fha = _load_inputs(intermediate_dir, fha_dir, hmda_dir, min_year, max_year)
    logger.info("Loaded HMDA: %s rows, FHA: %s records", f"{len(hmda):,}", f"{len(fha):,}")

    roster = pl.DataFrame(schema={"lei": pl.Utf8, "orig_num": pl.Utf8, "sponsor_num": pl.Utf8})
    rounds: list[pl.DataFrame] = []
    for number, round_ in enumerate(ROUNDS_POST2018, start=1):
        pairs = _run_round(round_, hmda, fha, roster)
        logger.info("Round %s (%s): %s pairs", number, round_.description, f"{len(pairs):,}")
        if round_.learns_roster:
            roster = pl.concat([roster, pairs.select(roster.columns)]).unique()
        rounds.append(
            pairs.select(
                "FHA_Index", "HMDAIndex", pl.lit(number, dtype=pl.UInt8).alias("match_round")
            )
        )
        hmda = hmda.join(pairs.select("HMDAIndex"), on="HMDAIndex", how="anti")
        fha = fha.join(pairs.select("FHA_Index"), on="FHA_Index", how="anti")

    crosswalk = pl.concat(rounds)
    for col in ("HMDAIndex", "FHA_Index"):
        if crosswalk[col].n_unique() != len(crosswalk):
            raise ValueError(f"crosswalk not 1:1 in {col}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / Config.crosswalk_name("post2018", min_year, max_year)
    with atomic_output(output_file) as tmp:
        crosswalk.write_parquet(tmp)
    logger.info("Saved crosswalk (%s pairs) to %s", f"{len(crosswalk):,}", output_file)
    return output_file
