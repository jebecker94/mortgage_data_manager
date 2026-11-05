"""HMDA-FHA pre-2018 (2010-2017) matching pipeline.

The post-2018 recipe cannot be ported to these years: pre-2018 HMDA carries no
``interest_rate``, which is the post-2018 matcher's most discriminating key
*and* the rate tolerance that does most of its disambiguation. A naive port
matches 0.8% of loans. What works instead, established empirically:

1. **Base key = ZIP + amount at $1k + purpose.** HMDA's ``census_tract`` is
   fanned to ZIP through the 2010-census ZIP-tract crosswalk (a 2.30x fan-out)
   and matched against FHA ``Property Zip``. Candidate density falls from 220.69
   FHA candidates per HMDA loan (county + $10k) to 2.66.
2. **The FHA pool is restricted to the round's endorsement window BEFORE the
   1:1 rule.** Endorsement lags origination by about a month, so the right pool
   is the activity year slid forward a month (Feb Y - Jan Y+1), not a wider one.
   Ordering is worth 4x: block-then-dedupe yields 401,680 pairs in 2015 against
   99,419 for dedupe-then-filter, because an out-of-window endorsement sharing
   the key destroys a pair the windowed pass never sees.
3. **1:1 = drop-all-ambiguous on both sides**, the same rule post-2018 uses.
4. **Bridge disambiguation.** Coverage on the base key is already ~95%, so the
   binding constraint is disambiguation, not candidate generation. The pre-2018
   FHA-lender <-> HMDA-respondent bridge removes inconsistent candidates on both
   sides before the 1:1 rule, lifting yield from 33.0% to 74.1% of HMDA FHA
   originations. The bridge is *silent, not negative* where it knows nothing, so
   the filter can only remove candidates.
5. **Margin tiers on the leftovers.** The last round takes every loan and
   record the bridge rounds left, over the window reopened at January, and
   resolves them with evidence those rounds never use: county agreement, the
   share of the HMDA tract lying in the candidate's ZIP (USPS residential
   ratio), and HMDA ``rate_spread`` against FHA's note rate. It accepts a
   candidate only when it leads every rival by a clear margin on *both* sides;
   resolving the same evidence by best score alone is measurably too loose for a
   crosswalk. It uses no lender identity, which keeps that a held-out check on it.

The rounds are `~mortgage_data_manager.matching.match_hmda_fha.rounds.ROUNDS_PRE2018`;
`run_rounds_pre2018` runs them.

Five pre-2018 encodings differ from post-2018 and every one of them fails
**silently** -- returning zero rows or, worse, plausible garbage:

- ``state_code`` is a 2-digit FIPS (``'02'``), not the postal abbreviation FHA
  carries in ``Property State``. FHA silver's ``state_fips`` column is the
  correct counterpart, and both sides are carried into the intermediates so a
  downstream geography check joins FIPS to FIPS.
- ``county_code`` is already the full 5-digit FIPS and joins FHA ``FIPS``
  directly (post-2018 needs no such note because its own coding differs).
  County is deliberately **not** a match key here: it is the held-out precision
  check the validation module uses, and margin-tier evidence only where a cell
  spans counties.
- ``loan_amount`` is already in dollars and is 100% exact thousands (only 9.13%
  divisible by $10,000). Post-2018's $10k-midpoint ``Loan Amount Round``
  destroys a factor of ten of real precision -- 0.8% clean 1:1 cells against
  26.7%. See `amount_key`.
- ``loan_purpose`` is 1 = purchase / 2 = home improvement / 3 = refi, **not**
  post-2018's 1 / 31 / 32. See `hmda_purpose_key`.
- ``respondent_id`` is encoded differently across the file vintages, so it is
  canonicalized on both sides through
  `~mortgage_data_manager.matching.match_hmda_fha.lender_bridge.hmda_respondent_key`.

Two further era facts that read as bugs if you meet them cold, both owned by
``hmda.load``: 2017 is the only pre-2018 year offering release ``a`` (every other
year is ``d`` alone), and every parquet file in the chosen partition must be read,
not only the first.

Output: ``crosswalk/hmda_fha/hmda_fha_crosswalk_pre2018.parquet``,
schema-identical to the post-2018 file so both eras union under one glob.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference import zip_tract
from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import (
    hmda_respondent_key,
    load_bridge,
)
from mortgage_data_manager.matching.match_hmda_fha.matching_post2018 import (
    get_zip_tract_for_year,
    hmda_tract_vintage,
)
from mortgage_data_manager.matching.match_hmda_fha.rounds import (
    ROUNDS_PRE2018,
    MarginTier,
    Pre2018Round,
)
from mortgage_data_manager.matching.resolve import drop_ambiguous, margin_best
from mortgage_data_manager.sources.hmda.load import scan_originations

logger = get_logger(__name__)

#: Intermediate-file suffix for this era.
FILE_SUFFIX = "_pre2018"

#: Base match key, HMDA side then FHA side. Same concepts, different source
#: spellings: FHA silver's ZIP column is ``Property Zip`` and is not renamed.
PRE2018_HMDA_KEYS = ["ZIP", "amount_key", "purpose"]
PRE2018_FHA_KEYS = ["Property Zip", "amount_key", "purpose"]

#: HMDA columns read from silver before the ZIP fan. ``census_tract`` (kept past
#: the fan) and ``rate_spread`` feed only the margin tiers.
_HMDA_READ_COLUMNS = [
    "HMDAIndex",
    "census_tract",
    "loan_amount",
    "loan_purpose",
    "state_code",
    "county_code",
    "respondent_id",
    "agency_code",
    "rate_spread",
]

#: FHA columns read from silver. ``Interest Rate`` feeds only the margin tiers.
_FHA_READ_COLUMNS = [
    "FHA_Index",
    "Property Zip",
    "Mortgage Amount",
    "Loan Purpose",
    "Year",
    "Month",
    "Originating Mortgagee Number",
    "Sponsor Number",
    "state_fips",
    "FIPS",
    "Interest Rate",
]

#: What the margin tiers need from the prepared intermediates; files prepared
#: before the tiers existed lack them.
_RECOVERY_HMDA_COLUMNS = ("census_tract", "rate_spread")
_RECOVERY_FHA_COLUMNS = ("Interest Rate",)

_PAIR_KEY = ["HMDAIndex", "FHA_Index"]


# =============================================================================
# Key expressions -- the era's encodings, defined once
# =============================================================================


def amount_key(col: str, granularity: int = Config.PRE2018_AMOUNT_GRANULARITY) -> pl.Expr:
    """Loan amount bucketed to ``granularity`` dollars, aliased ``amount_key``.

    Both sides are already in dollars pre-2018, and HMDA's are 100% exact
    thousands, so at the $1k default this is an identity relabelling that loses
    nothing. **Never substitute the post-2018 ``Loan Amount Round``
    expression** (``((x - 5000) / 10000).round() * 10000 + 5000``): it snaps to
    $10k midpoints, discards a factor of ten of real precision, and takes the
    share of loans in a clean 1:1 cell from 26.7% to 0.8%.

    Args:
        col: Dollar-denominated amount column (``loan_amount`` / ``Mortgage Amount``).
        granularity: Bucket width in dollars.

    Returns:
        An expression yielding the bucketed amount, aliased ``amount_key``.
    """
    return (pl.col(col) / granularity).round(0).cast(pl.Int64).alias("amount_key")


def hmda_purpose_key() -> pl.Expr:
    """HMDA ``loan_purpose`` mapped onto the shared purpose vocabulary.

    Pre-2018 codes are **1 = purchase, 2 = home improvement, 3 = refinance** --
    not post-2018's 1 / 31 / 32. Reusing the post-2018 mapping here silently
    sends every loan to ``other`` and costs the ~10pp the purpose key is worth.
    Home improvement joins ``other`` because FHA's enum has no counterpart for
    it, so it must not be allowed to match a refinance.

    Returns:
        An expression on the shared purpose vocabulary.
    """
    return (
        pl.when(pl.col("loan_purpose") == 1)
        .then(pl.lit("purchase"))
        .when(pl.col("loan_purpose") == 3)
        .then(pl.lit("refi"))
        .otherwise(pl.lit("other"))
        .alias("purpose")
    )


def fha_purpose_key() -> pl.Expr:
    """FHA ``Loan Purpose`` mapped onto the shared purpose vocabulary.

    ``Loan Purpose`` is an Enum in silver; casting to string first keeps the
    comparison independent of the category ordering.

    Returns:
        An expression on the shared purpose vocabulary.
    """
    purpose = pl.col("Loan Purpose").cast(pl.String)
    return (
        pl.when(purpose == "Purchase")
        .then(pl.lit("purchase"))
        .when(purpose.is_in(["Refi_FHA", "Refi_Conv_Curr"]))
        .then(pl.lit("refi"))
        .otherwise(pl.lit("other"))
        .alias("purpose")
    )


def endorsement_window(year: int, window: tuple[int, int]) -> pl.Expr:
    """FHA endorsement months inside ``window`` for HMDA activity year ``year``.

    ``window`` bounds the endorsement month minus January of ``year``, inclusive:
    ``(1, 12)`` admits exactly Feb ``year`` through Jan ``year + 1``. Widening it
    costs yield monotonically (~-1pp per extra month), because every extra month
    adds key-sharing endorsements that the drop-all-ambiguous rule then destroys
    pairs over.

    Args:
        year: HMDA activity year.
        window: Inclusive month-index bounds (see `Pre2018Round.window`).

    Returns:
        A boolean expression selecting the eligible endorsement months.
    """
    index = (pl.col("Year").cast(pl.Int32) - year) * 12 + pl.col("Month").cast(pl.Int32) - 1
    return index.is_between(*window)


# =============================================================================
# Data preparation
# =============================================================================


def hmda_fha_origination_counts(
    hmda_dir: Path,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
) -> dict[int, int]:
    """Raw HMDA FHA originations per year -- the honest match-rate denominator.

    Counted before the ZIP fan and before any null drop. The ZIP-joinable
    subset is ~0.4pp smaller and using it as the denominator inflates every
    published rate.

    Args:
        hmda_dir: HMDA ``period_2007_2017`` silver loans directory.
        min_year: First activity year to count.
        max_year: Last activity year to count.

    Returns:
        Raw FHA origination counts keyed by activity year.
    """
    counts: dict[int, int] = {}
    for year in range(min_year, max_year + 1):
        try:
            lf = scan_originations("period_2007_2017", year, year, loan_types=[2], root=hmda_dir)
        except FileNotFoundError as exc:
            logger.warning("Skipping year %s: %s", year, exc)
            continue
        counts[year] = int(lf.select(pl.len()).collect().item())
    return counts


@log_execution_time
def prepare_fha_pre2018(
    fha_dir: Path,
    intermediate_dir: Path,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
    file_suffix: str = FILE_SUFFIX,
) -> Path:
    """Prepare the FHA endorsement side for pre-2018 matching.

    Spans ``min_year`` through ``max_year + 1`` because the last year's window
    reaches into January of the following year. The per-year window itself is
    applied at match time, not here.

    Args:
        fha_dir: FHA single-family silver directory (hive-partitioned).
        intermediate_dir: Directory for intermediate match data.
        min_year: First HMDA activity year to be matched.
        max_year: Last HMDA activity year to be matched.
        file_suffix: Suffix for the intermediate file name.

    Returns:
        Path to the saved FHA match data file.
    """
    logger.info("Preparing FHA data for pre-2018 matching (years %s-%s)", min_year, max_year)

    lf = (
        pl.scan_parquet(fha_dir / "**/*.parquet")
        .filter(pl.col("Year").is_between(min_year, max_year + 1))
        .select(_FHA_READ_COLUMNS)
        .drop_nulls(["Property Zip", "Mortgage Amount"])
        .with_columns(
            amount_key("Mortgage Amount"),
            fha_purpose_key(),
            # Zero-padded 5-character strings in silver; the key expression is
            # read directly from the normalized silver columns.
            pl.col("Originating Mortgagee Number").alias("orig_id"),
            pl.col("Sponsor Number").alias("sponsor_id"),
        )
        .drop(["Mortgage Amount", "Loan Purpose", "Originating Mortgagee Number", "Sponsor Number"])
        # Sorted so the per-year window filter can skip row groups on rescan.
        .sort(["Year", "Month"])
    )

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    save_file = intermediate_dir / f"fha_match_data{file_suffix}.parquet"
    lf.sink_parquet(save_file)
    logger.info("Saved FHA match data to %s", save_file)
    return save_file


@log_execution_time
def prepare_hmda_pre2018(
    hmda_dir: Path,
    intermediate_dir: Path,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
    file_suffix: str = FILE_SUFFIX,
    crosswalk_dir: Path | None = None,
    usps_dir: Path | None = None,
) -> Path:
    """Prepare the HMDA origination side, fanned to ZIP, for pre-2018 matching.

    The census-tract to ZIP join is an **inner** join: a loan with no ZIP can
    never match on a ZIP key, and carrying it as a null row only inflates the
    intermediate. Each year joins the crosswalk of the census its tracts come
    from (2000-census tracts through 2011, 2010-census after), so the drop is
    loans whose tract HUD never mapped. The raw origination counts (the honest
    denominator) are logged here before any drop. ``census_tract`` is kept past the fan, because the margin
    tiers judge each fanned ZIP by the share of the tract it holds.

    Args:
        hmda_dir: HMDA ``period_2007_2017`` silver loans directory.
        intermediate_dir: Directory for intermediate match data.
        min_year: First activity year to include.
        max_year: Last activity year to include.
        file_suffix: Suffix for the intermediate file name.
        crosswalk_dir: Directory holding the ZIP-tract crosswalk.
        usps_dir: USPS crosswalk bronze directory (used if it must be built).

    Returns:
        Path to the saved HMDA match data file.

    Raises:
        ValueError: If no HMDA data was found in the requested range.
    """
    logger.info("Preparing HMDA data for pre-2018 matching (years %s-%s)", min_year, max_year)

    zip_tracts: dict[int, pl.DataFrame] = {}
    frames: list[pl.LazyFrame] = []
    for year in range(min_year, max_year + 1):
        try:
            lf = scan_originations("period_2007_2017", year, year, loan_types=[2], root=hmda_dir)
        except FileNotFoundError as exc:
            logger.warning("Skipping year %s: %s", year, exc)
            continue
        logger.info("Reading HMDA %s", year)
        vintage = hmda_tract_vintage(year)
        if vintage not in zip_tracts:
            crosswalk_file = get_zip_tract_for_year(year, crosswalk_dir, usps_dir)
            zip_tracts[vintage] = pl.read_parquet(crosswalk_file).select("ZIP", "TRACT").unique()
            logger.info(
                "ZIP-tract crosswalk: %s pairs from %s",
                f"{zip_tracts[vintage].height:,}",
                crosswalk_file,
            )

        frames.append(
            lf.select(_HMDA_READ_COLUMNS)
            .drop_nulls(["census_tract", "loan_amount"])
            .with_columns(
                pl.lit(year).cast(pl.Int16).alias("activity_year"),
                amount_key("loan_amount"),
                hmda_purpose_key(),
                hmda_respondent_key(),
            )
            .drop(["loan_amount", "loan_purpose", "respondent_id"])
            .join(zip_tracts[vintage].lazy(), left_on="census_tract", right_on="TRACT", how="inner")
        )

    if not frames:
        raise ValueError(f"No HMDA data found for years {min_year}-{max_year}")

    # diagonal: the pre-2018 vintages order the same columns differently.
    df = pl.concat(frames, how="diagonal").sort("activity_year")

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    save_file = intermediate_dir / f"hmda_match_data{file_suffix}.parquet"
    df.sink_parquet(save_file)
    logger.info("Saved HMDA match data to %s", save_file)
    return save_file


# =============================================================================
# Matching
# =============================================================================


def build_candidate_pairs(df_hmda: pl.DataFrame, df_fha: pl.DataFrame) -> pl.DataFrame:
    """Join one year's HMDA and FHA pools on the base key.

    Deduplicated on ``(HMDAIndex, FHA_Index)`` because the ZIP fan puts one loan
    in several rows; counting per cell instead overstates cleanliness by about
    6pp.

    Args:
        df_hmda: One activity year of the prepared, ZIP-fanned HMDA pool.
        df_fha: The FHA pool already restricted to that year's window.

    Returns:
        One row per distinct ``(HMDAIndex, FHA_Index)`` candidate pair.
    """
    return (
        df_hmda.select(["HMDAIndex", *PRE2018_HMDA_KEYS])
        .join(
            df_fha.select(["FHA_Index", *PRE2018_FHA_KEYS]),
            left_on=PRE2018_HMDA_KEYS,
            right_on=PRE2018_FHA_KEYS,
            how="inner",
        )
        .select(_PAIR_KEY)
        .unique()
    )


def attach_lender_identities(
    pairs: pl.DataFrame, df_hmda: pl.DataFrame, df_fha: pl.DataFrame
) -> pl.DataFrame:
    """Add each pair's HMDA respondent key and FHA lender ids.

    Both sides are deduplicated before the join: the HMDA pool is ZIP-fanned, so
    joining it directly would multiply candidate rows.

    Args:
        pairs: The candidate pairs.
        df_hmda: The prepared HMDA side.
        df_fha: The prepared FHA side.

    Returns:
        The pairs with the HMDA respondent key and FHA lender ids attached.
    """
    return pairs.join(
        df_hmda.select("HMDAIndex", "resp_key").unique(), on="HMDAIndex", how="left"
    ).join(df_fha.select("FHA_Index", "orig_id", "sponsor_id").unique(), on="FHA_Index", how="left")


def mark_bridge_consistent(candidates: pl.DataFrame, bridge: pl.DataFrame) -> pl.DataFrame:
    """Add ``bridge_ok``, flagging pairs whose FHA lender is bridged to their HMDA respondent.

    An entry filed under either role is accepted for either FHA id, because the
    mortgagee number is one registry and the originating/sponsor split is a
    property of the loan, not of the institution. The permissive rule was
    measured against the role-strict one in the feasibility work and is what the
    74.0% headline is built on.

    Entries are applied **year-blind**: the bridge's ``first_year``/``last_year``
    span is carried but not enforced, which is the configuration the
    frozen-artifact agreement (99.8%) and placebo evidence were established at.

    The flag is attached by join and returned on the frame, never as a detached
    Series -- polars does not guarantee join output order, so re-attaching a
    loose column positionally would silently scramble it.

    Args:
        candidates: The candidate pairs.
        bridge: The lender bridge to test consistency against.

    Returns:
        The candidates with a ``bridge_ok`` column added.
    """
    originating = (
        bridge.select(pl.col("fha_id").alias("orig_id"), "resp_key")
        .unique()
        .with_columns(pl.lit(value=True).alias("_ok_orig"))
    )
    sponsor = (
        bridge.select(pl.col("fha_id").alias("sponsor_id"), "resp_key")
        .unique()
        .with_columns(pl.lit(value=True).alias("_ok_sponsor"))
    )
    return (
        candidates.join(originating, on=["orig_id", "resp_key"], how="left")
        .join(sponsor, on=["sponsor_id", "resp_key"], how="left")
        .with_columns(
            (
                pl.col("_ok_orig").fill_null(value=False)
                | pl.col("_ok_sponsor").fill_null(value=False)
            ).alias("bridge_ok")
        )
        .drop("_ok_orig", "_ok_sponsor")
    )


def drop_bridge_inconsistent(marked: pl.DataFrame) -> pl.DataFrame:
    """Drop bridge-inconsistent candidates wherever a consistent one exists.

    Applied to **both** sides and **before** the 1:1 rule. A loan with no
    consistent candidate keeps its entire candidate set -- the bridge is silent,
    not negative -- so this filter can only remove pairs, and can therefore only
    turn ambiguous cells into clean ones. Baseline 1:1 pairs are provably
    untouched: both of their sides hold exactly one candidate, so neither filter
    can reach them.

    Args:
        marked: Candidate pairs already marked for bridge consistency.

    Returns:
        The candidates with inconsistent pairs dropped wherever a consistent one exists.
    """
    return (
        marked.with_columns(
            pl.col("bridge_ok").any().over("HMDAIndex").alias("_hmda_any"),
            pl.col("bridge_ok").any().over("FHA_Index").alias("_fha_any"),
        )
        .filter(
            (pl.col("bridge_ok") | ~pl.col("_hmda_any"))
            & (pl.col("bridge_ok") | ~pl.col("_fha_any"))
        )
        .drop("_hmda_any", "_fha_any")
    )


# =============================================================================
# Margin-tier evidence
# =============================================================================

#: HMDA ``rate_spread`` (APR - APOR, reported when >= 1.5pp) against the FHA note
#: rate, net of the month's median offset: within RATE_AGREE pp agrees, beyond
#: RATE_DISAGREE pp disagrees.
RATE_AGREE = 0.25
RATE_DISAGREE = 0.5
#: An unreported spread says APR - APOR < 1.5pp, so an FHA rate implying a spread
#: above this (1.5pp plus slack for the month proxy) disagrees too.
SPREAD_CAP = 1.75
#: A clear rate winner deviates by at most RATE_CLEAR pp, and every rival by
#: RATE_CLEAR_MARGIN pp more.
RATE_CLEAR = 0.125
RATE_CLEAR_MARGIN = 0.375
#: A ZIP holding at least this share of the tract's residential addresses is the
#: tract's dominant ZIP.
DOMINANT_ZIP_SHARE = 0.5
#: Last USPS quarter-year averaged into the residential ratios: the 2010-census
#: quarters up to the era's last endorsement window (Jan 2018). Fixed rather than
#: tied to the requested years, so a year's result does not depend on the range run.
RESIDENTIAL_RATIO_LAST_YEAR = 2018

#: Each `MarginTier`: an evidence score, the margin its winner must hold over every rival
#: on both sides, and a condition on the winner itself.
_MARGIN_TIERS: dict[MarginTier, tuple[pl.Expr, float, pl.Expr]] = {
    "county": (pl.col("county_eq").cast(pl.Int8), 1, pl.lit(value=True)),
    "geo_rate": (pl.col("s_geo") + pl.col("s_rate"), 2, pl.lit(value=True)),
    "rate": (-pl.col("rate_dev").abs(), RATE_CLEAR_MARGIN, pl.col("rate_dev").abs() <= RATE_CLEAR),
}


def tract_zip_residential_ratio(usps_dir: Path = Config.USPS_CROSSWALK_DIR) -> pl.DataFrame:
    """Share of each tract's residential addresses in each ZIP, per census.

    HUD's USPS TRACT_ZIP ``res_ratio``, averaged over each census's quarters: the
    2000-census quarters (2010-2011) and the 2010-census quarters through
    `RESIDENTIAL_RATIO_LAST_YEAR`. A tract code can name different areas in the two
    censuses, so a loan reads the ratios of the census its year's tracts come from.
    The ZIP fan makes a tract's minor ZIPs candidates as readily as its dominant one;
    this says how plausible each is.

    Args:
        usps_dir: USPS crosswalk bronze directory.

    Returns:
        ``census_tract``, ``ZIP``, ``tract_vintage`` and the mean ``res_ratio``.
    """
    return zip_tract.tract_zip_residential_ratio(
        (2000, 2010), last_year=RESIDENTIAL_RATIO_LAST_YEAR, data_dir=usps_dir
    )


def rate_spread_offset(pairs: pl.DataFrame, hmda: pl.DataFrame, fha: pl.DataFrame) -> pl.DataFrame:
    """Median ``Interest Rate - rate_spread`` per endorsement month on learning-round pairs.

    ``rate_spread`` is APR minus the average prime offer rate at rate lock, and
    neither source carries APOR or the lock date. The month's median offset on
    pairs matched without any rate evidence stands in for both.

    Args:
        pairs: Pairs of the rounds flagged ``learns_rate_offset``.
        hmda: Per-loan HMDA attributes, with ``rate_spread``.
        fha: FHA attributes, with ``Year``, ``Month`` and ``Interest Rate``.

    Returns:
        ``Year``, ``Month`` and ``rate_offset``.
    """
    return (
        pairs.select(_PAIR_KEY)
        .join(hmda.select("HMDAIndex", "rate_spread"), on="HMDAIndex")
        .join(fha.select("FHA_Index", "Year", "Month", "Interest Rate"), on="FHA_Index")
        .filter(pl.col("rate_spread").is_not_null())
        .group_by("Year", "Month")
        .agg((pl.col("Interest Rate") - pl.col("rate_spread")).median().alias("rate_offset"))
    )


def attach_recovery_evidence(
    pairs: pl.DataFrame,
    hmda: pl.DataFrame,
    fha: pl.DataFrame,
    residential: pl.DataFrame,
    offset: pl.DataFrame,
) -> pl.DataFrame:
    """Add the margin tiers' evidence to candidate pairs.

    - ``county_eq``: HMDA county = FHA county. Never a match key, so informative
      wherever a cell spans counties.
    - ``res_ratio``: the share of the HMDA tract's residential addresses in the
      FHA record's ZIP, in the census of the loan's year (0 where the tract does not
      reach it).
    - ``rate_dev``: FHA note rate - HMDA ``rate_spread`` - the month's offset;
      null where HMDA reported no spread.
    - ``s_geo`` (0 to 2): county agreement plus a dominant ZIP.
    - ``s_rate`` (-1 to 1): +1 within `RATE_AGREE`, -1 beyond `RATE_DISAGREE`;
      an unreported spread is -1 when the FHA rate implies one above `SPREAD_CAP`.

    Args:
        pairs: Candidate pairs, with ``activity_year``.
        hmda: Per-loan HMDA attributes (``census_tract``, ``county_code``, ``rate_spread``).
        fha: FHA attributes (``Property Zip``, ``FIPS``, ``Year``, ``Month``, ``Interest Rate``).
        residential: `tract_zip_residential_ratio`.
        offset: `rate_spread_offset`.

    Returns:
        The pairs with the evidence columns added.
    """
    rate_dev = pl.col("rate_dev").abs()
    vintages = {y: hmda_tract_vintage(y) for y in pairs["activity_year"].unique().to_list()}
    return (
        pairs.with_columns(
            pl.col("activity_year")
            .replace_strict(vintages, return_dtype=pl.Int16)
            .alias("tract_vintage")
        )
        .join(hmda, on="HMDAIndex", how="left")
        .join(fha, on="FHA_Index", how="left")
        .join(
            residential,
            left_on=["census_tract", "Property Zip", "tract_vintage"],
            right_on=["census_tract", "ZIP", "tract_vintage"],
            how="left",
        )
        .drop("tract_vintage")
        .join(offset, on=["Year", "Month"], how="left")
        .with_columns(
            (pl.col("county_code") == pl.col("FIPS")).fill_null(value=False).alias("county_eq"),
            pl.col("res_ratio").fill_null(0.0),
            (pl.col("Interest Rate") - pl.col("rate_spread") - pl.col("rate_offset")).alias(
                "rate_dev"
            ),
            (pl.col("Interest Rate") - pl.col("rate_offset")).alias("implied_spread"),
        )
        .with_columns(
            (
                pl.col("county_eq").cast(pl.Int8)
                + (pl.col("res_ratio") >= DOMINANT_ZIP_SHARE).cast(pl.Int8)
            ).alias("s_geo"),
            pl.when(pl.col("rate_spread").is_null())
            .then(pl.when(pl.col("implied_spread") > SPREAD_CAP).then(-1).otherwise(0))
            .when(rate_dev <= RATE_AGREE)
            .then(1)
            .when(rate_dev > RATE_DISAGREE)
            .then(-1)
            .otherwise(0)
            .cast(pl.Int8)
            .alias("s_rate"),
        )
    )


def _require_recovery_columns(hmda_file: Path, fha_file: Path) -> None:
    """Fail fast on intermediates prepared before the margin-tier evidence existed."""
    for path, needed in (
        (hmda_file, _RECOVERY_HMDA_COLUMNS),
        (fha_file, _RECOVERY_FHA_COLUMNS),
    ):
        missing = [c for c in needed if c not in pl.read_parquet_schema(path)]
        if missing:
            raise ValueError(
                f"{path.name} lacks {missing}: it was prepared before the margin tiers "
                "existed. Rebuild the intermediates with overwrite=True (CLI: --overwrite)."
            )


# =============================================================================
# Rounds
# =============================================================================


def _candidates(round_: Pre2018Round, hmda: pl.DataFrame, fha: pl.DataFrame) -> pl.DataFrame:
    """The round's base-key candidates, each activity year against its own window.

    Args:
        round_: The round whose ``window`` applies.
        hmda: Unclaimed prepared HMDA rows.
        fha: Unclaimed prepared FHA records.

    Returns:
        Candidate pairs with ``activity_year``.
    """
    return pl.concat(
        [
            build_candidate_pairs(
                hmda.filter(pl.col("activity_year") == year),
                fha.filter(endorsement_window(year, round_.window)),
            ).with_columns(pl.lit(year).cast(pl.Int16).alias("activity_year"))
            for year in sorted(hmda["activity_year"].unique().to_list())
        ]
    )


def _resolve_margin_tiers(evidence: pl.DataFrame, tiers: tuple[MarginTier, ...]) -> pl.DataFrame:
    """Each tier's clear winners, minus any loan or record two tiers pair differently.

    Each tier is resolved on its own with `margin_best`; a loan or record that two tiers
    assign to different partners is then dropped, so the round stays 1:1 without ranking
    one tier above another.

    Args:
        evidence: Candidate pairs from `attach_recovery_evidence`, with ``activity_year``.
        tiers: The round's tiers.

    Returns:
        The round's pairs, with ``activity_year``.
    """
    winners = pl.concat(
        [
            margin_best(
                evidence.with_columns(score.alias("_tier_score")),
                "_tier_score",
                "HMDAIndex",
                "FHA_Index",
                margin=margin,
                descending=True,
            )
            .filter(accept)
            .select(*_PAIR_KEY, "activity_year")
            for score, margin, accept in (_MARGIN_TIERS[tier] for tier in tiers)
        ]
    ).unique()
    return drop_ambiguous(winners, "FHA_Index", "HMDAIndex")


@log_execution_time
def run_rounds_pre2018(
    hmda: pl.DataFrame,
    fha: pl.DataFrame,
    rounds: tuple[Pre2018Round, ...] = ROUNDS_PRE2018,
    bridge: pl.DataFrame | None = None,
    usps_dir: Path = Config.USPS_CROSSWALK_DIR,
) -> pl.DataFrame:
    """Run ``rounds`` in order, each on the loans and records earlier rounds left.

    Resolution pools every activity year, so an FHA record inside two years' windows is
    judged against the loans of both. The rate offset a margin tier nets out is learned
    from the pairs of every earlier round flagged ``learns_rate_offset``.

    Args:
        hmda: The prepared, ZIP-fanned HMDA pool.
        fha: The prepared FHA endorsement pool.
        rounds: The rounds to run.
        bridge: Lender bridge entries (``fha_id``, ``resp_key``); required when a round
            has ``bridge_filter``.
        usps_dir: USPS crosswalk bronze directory, read when a round has margin tiers.

    Returns:
        ``FHA_Index``, ``HMDAIndex``, ``activity_year`` and ``match_round``.

    Raises:
        ValueError: If a round needs a bridge or a rate offset it was not given.
    """
    if bridge is None and any(r.bridge_filter for r in rounds):
        raise ValueError("a round with bridge_filter needs the lender bridge")
    hmda_attr = fha_attr = residential = None
    if any(r.tiers for r in rounds):
        # A loan's prepared rows repeat per candidate ZIP but agree on these four
        # columns, so the whole-row dedup collapses identical rows only.
        hmda_attr = hmda.select("HMDAIndex", "census_tract", "county_code", "rate_spread").unique()
        fha_attr = fha.select("FHA_Index", "Property Zip", "FIPS", "Year", "Month", "Interest Rate")
        residential = tract_zip_residential_ratio(usps_dir)

    frames: list[pl.DataFrame] = []
    learned: list[pl.DataFrame] = []
    hmda_left, fha_left = hmda, fha
    for number, round_ in enumerate(rounds, start=1):
        candidates = _candidates(round_, hmda_left, fha_left)
        if round_.bridge_filter:
            assert bridge is not None
            marked = mark_bridge_consistent(
                attach_lender_identities(candidates, hmda_left, fha_left), bridge
            )
            candidates = drop_bridge_inconsistent(marked).select(*_PAIR_KEY, "activity_year")
        if round_.tiers:
            if not learned:
                raise ValueError(
                    f"round {number} ({round_.description}) has margin tiers but no earlier "
                    "round learns the rate offset"
                )
            assert hmda_attr is not None and fha_attr is not None and residential is not None
            offset = rate_spread_offset(pl.concat(learned), hmda_attr, fha_attr)
            evidence = attach_recovery_evidence(
                candidates, hmda_attr, fha_attr, residential, offset
            )
            pairs = _resolve_margin_tiers(evidence, round_.tiers)
        else:
            pairs = drop_ambiguous(candidates, "FHA_Index", "HMDAIndex")
        pairs = pairs.select(*_PAIR_KEY, "activity_year")
        if round_.learns_rate_offset:
            learned.append(pairs)
        logger.info(
            "Round %d (%s): %s pairs from %s candidates",
            number,
            round_.description,
            f"{pairs.height:,}",
            f"{candidates.height:,}",
        )
        frames.append(pairs.with_columns(pl.lit(number, dtype=pl.UInt8).alias("match_round")))
        hmda_left = hmda_left.join(pairs.select("HMDAIndex"), on="HMDAIndex", how="anti")
        fha_left = fha_left.join(pairs.select("FHA_Index"), on="FHA_Index", how="anti")
    return pl.concat(frames)


def _log_yields(crosswalk: pl.DataFrame, denominators: dict[int, int], n_rounds: int) -> None:
    """Log each year's pairs by round against its raw HMDA FHA originations."""
    by_year = crosswalk.group_by("activity_year", "match_round").len()
    header = "".join(f"{f'round {r}':>12}" for r in range(1, n_rounds + 1))
    logger.info("  %6s %14s%s %12s %9s", "year", "HMDA FHA orig", header, "total", "rate")
    for year in sorted(denominators):
        counts = [
            int(
                by_year.filter((pl.col("activity_year") == year) & (pl.col("match_round") == r))[
                    "len"
                ].sum()
            )
            for r in range(1, n_rounds + 1)
        ]
        denominator = denominators[year]
        logger.info(
            "  %6d %14s%s %12s %8.1f%%",
            year,
            f"{denominator:,}",
            "".join(f"{c:>12,}" for c in counts),
            f"{sum(counts):,}",
            sum(counts) / denominator * 100 if denominator else 0.0,
        )
    total = sum(denominators.values())
    logger.info(
        "  TOTAL %s pairs, %.1f%% of %s HMDA FHA originations",
        f"{crosswalk.height:,}",
        crosswalk.height / total * 100 if total else 0.0,
        f"{total:,}",
    )


# =============================================================================
# Orchestration
# =============================================================================


@log_execution_time
def run_matching_pre2018(
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
    fha_dir: Path | None = None,
    hmda_dir: Path | None = None,
    intermediate_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Run the pre-2018 HMDA-FHA matching pipeline.

    Prepares the FHA endorsement pool and the ZIP-fanned HMDA origination pool, runs
    `ROUNDS_PRE2018` (each round on the earlier rounds' leftovers) and writes one
    crosswalk tagged with ``match_round``. It is written schema-identical to the
    post-2018 crosswalk (``FHA_Index``, ``HMDAIndex``, ``match_round``), so a consumer
    wanting both eras can glob ``hmda_fha_crosswalk_*.parquet``. The eras stay in
    separate files because they rebuild on different cadences (pre-2018's sources are
    closed).

    Args:
        min_year: First HMDA activity year to match.
        max_year: Last HMDA activity year to match.
        fha_dir: FHA single-family silver directory.
        hmda_dir: HMDA ``period_2007_2017`` silver loans directory.
        intermediate_dir: Intermediate match data directory.
        output_dir: Published crosswalk directory.
        overwrite: Rebuild intermediates that already exist.

    Returns:
        Path to the published crosswalk file.

    Raises:
        ValueError: If no HMDA loans were prepared, or the crosswalk is not 1:1.
    """
    logger.info("Starting pre-2018 HMDA-FHA matching pipeline (years %s-%s)", min_year, max_year)

    fha_dir = Config.FHA_SILVER_DIR if fha_dir is None else fha_dir
    hmda_dir = Config.HMDA_PRE2018_SILVER_DIR if hmda_dir is None else hmda_dir
    intermediate_dir = (
        Config.HMDA_FHA_INTERMEDIATE_DIR if intermediate_dir is None else intermediate_dir
    )
    output_dir = Config.HMDA_FHA_CROSSWALK_DIR if output_dir is None else output_dir

    Config.ensure_directories()

    fha_file = intermediate_dir / f"fha_match_data{FILE_SUFFIX}.parquet"
    if should_process_file(fha_file, overwrite):
        prepare_fha_pre2018(fha_dir, intermediate_dir, min_year, max_year, FILE_SUFFIX)
    else:
        logger.info("Skipping FHA prep - file exists: %s", fha_file)

    hmda_file = intermediate_dir / f"hmda_match_data{FILE_SUFFIX}.parquet"
    if should_process_file(hmda_file, overwrite):
        prepare_hmda_pre2018(hmda_dir, intermediate_dir, min_year, max_year, FILE_SUFFIX)
    else:
        logger.info("Skipping HMDA prep - file exists: %s", hmda_file)

    if any(r.tiers for r in ROUNDS_PRE2018):
        _require_recovery_columns(hmda_file, fha_file)

    bridge = None
    if any(r.bridge_filter for r in ROUNDS_PRE2018):
        bridge = load_bridge("merged").select("fha_id_type", "fha_id", "resp_key")
        logger.info("Loaded lender bridge: %s entries", f"{bridge.height:,}")

    # The intermediates span whatever range they were prepared for; match the requested one.
    hmda = pl.scan_parquet(hmda_file).filter(pl.col("activity_year").is_between(min_year, max_year))
    hmda_df = hmda.collect()
    if hmda_df.height == 0:
        raise ValueError(f"No prepared HMDA loans for years {min_year}-{max_year}")
    crosswalk = run_rounds_pre2018(hmda_df, pl.read_parquet(fha_file), ROUNDS_PRE2018, bridge)
    _log_yields(
        crosswalk,
        hmda_fha_origination_counts(hmda_dir, min_year, max_year),
        len(ROUNDS_PRE2018),
    )

    df = crosswalk.select("FHA_Index", "HMDAIndex", "match_round")
    for col in ("HMDAIndex", "FHA_Index"):
        if df[col].n_unique() != len(df):
            raise ValueError(f"pre-2018 crosswalk not 1:1 in {col}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / Config.crosswalk_name("pre2018", min_year, max_year)
    with atomic_output(output_file) as tmp:
        df.write_parquet(tmp)
    logger.info("Saved crosswalk (%s pairs) to %s", f"{len(df):,}", output_file)
    return output_file
