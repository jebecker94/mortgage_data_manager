"""Pre-2018 FHA-lender ↔ HMDA-respondent bridge, the lender identity the pre-2018 matcher uses.

Answers "which HMDA respondent is FHA mortgagee 64119, and in which years?" for
the 2010–2017 era, where HMDA carries no ``lei`` and the two sources share no
institution identifier. Post-2018, the direct HMDA↔issuance matcher bridges LEIs to
seller names with `~mortgage_data_manager.matching.match_hmda_gse.lender_names`.

**The name route never reads a match artifact, and that is structural, not a
convention.** `build_name_route` takes only source directories — FHA
silver, the HMDA panel, the HMDA loan register — so it cannot consume a
crosswalk even by accident. Any future learned route (co-occurrence fitted on
already-matched pairs) must take that pairs frame as a *required argument*, so a
reader can tell the two apart from the signatures alone. This separation is the
entire basis of the circularity-free headline the pre-2018 matcher publishes:
the name route lifts its yield from 32.8% to 74.0% of HMDA FHA originations
without ever having seen a matched pair.

Three tiers of evidence, best first:

0. **The deterministic ID embedding.** For agency-code 7 (HUD) filers, the HMDA
   ``respondent_id`` in its 10-digit numeric form *contains the 5-digit FHA
   mortgagee number as its prefix* (``1100900005`` → mortgagee ``11009``). Exact,
   threshold-free, and it reaches d/b/a pairs that share zero name tokens —
   FHA's ``SFMC LP`` against HMDA's ``SERVICE FIRST MORTGAGE COMPANY``. Against a
   shuffled placebo these pairs score mean name-Jaccard 0.840 vs 0.126.
1. **Exact normalized-name equality**, over every name variant each side ever
   used, through the shared `~mortgage_data_manager.matching.names` helpers.
2. **Token-set Jaccard ≥ 0.70.** The cut is set on evidence, not precedent:
   below it the out-of-sample state-agreement proxy is indistinguishable from
   its 3.6% random-pair base rate, and tier-0 concordance falls away.
3. **The audit residual** — FHA lenders no rule resolved, carrying their best
   candidate so the misses can be read. Tier 3 is *not* a bridge entry; it lives
   only in the names-route artifact.

Most residual ambiguity turned out to be temporal rather than real: pre-2018
HMDA respondent identity is unstable (HUD-form id → EIN around 2011, agency
reassignments, the CFPB handover), so one firm legitimately holds several
respondent keys in *disjoint* years. Those are emitted as era-tagged entries
carrying their observed ``first_year``/``last_year`` span rather than dropped,
which moved coverage from 73.3% to 87.6% of pre-2018 FHA endorsements.

**Rebuild cadence: on a method change or an upstream silver rebuild only.** Both
pre-2018 sources are closed — HMDA ``period_2007_2017`` and the FHA endorsement
history for those years are final — so this artifact must not be wired into the
annual HMDA refresh.

Artifacts, under ``HMDAFHAMatchingConfig.get_bridge_path``:

- ``hmda_fha_lender_bridge_names_pre2018`` — the whole name route, tiers 0–3.
- ``hmda_fha_lender_bridge_pre2018`` — the bridge proper: tiers 0–2, one row per
  ``(fha_id_type, fha_id, resp_key, agency_code)``, with route provenance.
- ``hmda_fha_lender_bridge_learned_pre2018`` — reserved for the learned route.
  That stem, not a synonym, when it ships.

``in_learned_route`` / ``provenance`` / ``merge_rule`` are constant in this
version because only one route is built. They ship anyway: they are the hook a
learned route plugs into, and a column a consumer will need must exist from the
first write. There is deliberately no single ``route`` column: it would record
which row survived deduplication rather than where the evidence came from, and so
mislabel every lender the routes agree on. `_check_provenance` raises rather than
let the route flags and ``provenance`` drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig
from mortgage_data_manager.matching.names import jaccard, name_tokens, normalize_name
from mortgage_data_manager.sources.fha.config import FHAConfig
from mortgage_data_manager.sources.hmda.config import HMDAConfig
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)

#: Bridge artifact stems.
NAMES_ROUTE_NAME = "hmda_fha_lender_bridge_names_pre2018"
BRIDGE_NAME = "hmda_fha_lender_bridge_pre2018"

#: Requested year range: HMDA activity years the bridge is built over. The
#: matcher's own era range is configured separately and passed in.
PRE2018_MIN_YEAR = 2010
PRE2018_MAX_YEAR = 2017

#: Tier-2 cut. Set from the state-agreement and tier-0 concordance evidence,
#: not from post-2018 precedent. Lowering it admits a band that is
#: indistinguishable from noise on both proxies; the right fix for the true pairs
#: it excludes is a hand-adjudicated override list.
JACCARD_THRESHOLD = 0.70

#: Scores below this are never materialized. Far under any plausible cut; it
#: exists so the distribution can be inspected down into the noise.
JACCARD_FLOOR = 0.30

#: HMDA agency code 7 = HUD. Only its filers carry the embedded mortgagee number.
HUD_AGENCY_CODE = 7

#: The HUD-form ``respondent_id`` shape whose first five digits are the FHA
#: mortgagee number. Applied to the panel's RAW id: `hmda_respondent_key`
#: strips leading zeros, which would destroy the prefix.
_HUD_NUMERIC_ID = r"^\d{10}$"

#: FHA silver's two lender roles, in the one vocabulary this bridge uses (never
#: ``orig`` or ``master``); ``master`` is not an id type at all but the matcher-side
#: ``coalesce(originating, sponsor)`` fallback, and must never appear here.
_FHA_ID_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("originating", "Originating Mortgagee Number", "Originating Mortgagee"),
    ("sponsor", "Sponsor Number", "Sponsor Name"),
)

#: Entity keys. ``respondent_id`` is unique only WITHIN ``agency_code``, so the
#: HMDA key is the pair -- one institution can even hold the same id under two
#: agency codes across the era (Freedom Mortgage moved from 7 to 9 in 2016).
_FHA_KEY = ["fha_id_type", "fha_id"]
_HMDA_KEY = ["resp_key", "agency_code"]

FHA_SF_SILVER_DIR: Path = FHAConfig.FHA_SILVER_DIR / "single_family"
HMDA_PRE2018_PANEL_DIR: Path = HMDAConfig.get_dataset_dir("silver", "panel", "period_2007_2017")
HMDA_PRE2018_LOANS_DIR: Path = HMDAConfig.get_dataset_dir("silver", "loans", "period_2007_2017")


# --------------------------------------------------------------------------- #
# Shared key expressions (imported by the pre-2018 matcher -- define once)
# --------------------------------------------------------------------------- #


def hmda_respondent_key(col: str = "respondent_id") -> pl.Expr:
    """Canonical pre-2018 HMDA respondent key, aliased ``resp_key``.

    ``respondent_id`` is encoded differently across the pre-2018 file vintages:
    ``file_type=d`` writes zero-padded numerics and hyphenated tax ids
    (``0000013303``, ``63-1005978``), ``file_type=a`` writes neither (``13303``,
    ``631005978``). 2017 is the only year offering ``a`` and the era's file-type
    preference selects it, so a pooled pre-2018 lender analysis silently loses
    2017 without this: only 113 of 1,658 respondent keys carry over from 2016,
    against 1,412 once normalized. Normalizing collides zero distinct keys within
    any year, in both the loan files and the panel, so it is safe and required.

    The panel is not ``file_type``-partitioned and uses the ``d`` encoding
    throughout, so panel joins need this too — on both sides, always.

    An all-zero id normalizes to the empty string, which callers drop.

    Args:
        col: Column holding the raw ``respondent_id``.

    Returns:
        The canonical key expression, aliased ``resp_key``.
    """
    return (
        pl.col(col)
        .str.replace_all(r"[^A-Za-z0-9]", "")
        .str.strip_chars_start("0")
        .alias("resp_key")
    )


# --------------------------------------------------------------------------- #
# FHA side
# --------------------------------------------------------------------------- #


def fha_endorsement_years(min_year: int, max_year: int) -> tuple[int, int]:
    """FHA endorsement years the matcher's Feb Y – Jan Y+1 windows can touch.

    One year of lead-in below the requested range so a lender first seen in a
    partial window still gets its name variants, and one above so the Jan Y+1
    tail is covered.

    Args:
        min_year: First requested activity year.
        max_year: Last requested activity year.

    Returns:
        The inclusive endorsement-year bounds to scan.
    """
    return min_year - 1, max_year + 1


def _fha_lender_rows(fha_silver_dir: Path, first_year: int, last_year: int) -> pl.DataFrame:
    """One row per (endorsement, lender role): id, inline name, state, year."""
    endorsements = pl.scan_parquet(fha_silver_dir / "**/*.parquet").filter(
        pl.col("Year").is_between(first_year, last_year)
    )
    parts = [
        endorsements.filter(pl.col(num_col).is_not_null()).select(
            pl.lit(id_type).alias("fha_id_type"),
            pl.col(num_col).alias("fha_id"),
            pl.col(name_col).str.strip_chars().alias("fha_name"),
            pl.col("Property State").alias("property_state"),
            pl.col("Year").alias("year"),
        )
        for id_type, num_col, name_col in _FHA_ID_COLUMNS
    ]
    return pl.concat(parts, how="diagonal_relaxed").collect(engine="streaming")


def _modal(rows: pl.DataFrame, col: str) -> pl.DataFrame:
    """Most frequent value of ``col`` per FHA lender, or null when the plurality is tied.

    A tied plurality says nothing about which value is the lender's, so none is
    returned: a lender with no modal state cannot resolve candidates by state
    agreement, and one with no modal name carries no canonical ``fha_name``.
    Picking one of the tied values -- by the value itself or by row order -- would
    manufacture that evidence.

    Returns:
        One row per FHA lender, carrying its modal value of ``col`` or null.
    """
    counts = rows.group_by([*_FHA_KEY, col]).len()
    modal = counts.filter(pl.col("len") == pl.col("len").max().over(_FHA_KEY)).filter(
        pl.len().over(_FHA_KEY) == 1
    )
    return (
        counts.select(_FHA_KEY)
        .unique()
        .join(modal.select([*_FHA_KEY, col]), on=_FHA_KEY, how="left")
    )


def _fha_lender_universe(rows: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """FHA lender entities and their name variants, keyed ``(fha_id_type, fha_id)``."""
    named = rows.filter(pl.col("fha_name").str.len_chars() > 0)
    entities = (
        rows.group_by(_FHA_KEY)
        .agg(
            fha_loans=pl.len(),
            n_states=pl.col("property_state").n_unique(),
        )
        # Modal property state is the only geographic signal FHA carries; it is
        # never a match key, which is what makes it usable as a precision proxy.
        .join(
            _modal(rows.drop_nulls("property_state"), "property_state").rename(
                {"property_state": "fha_state"}
            ),
            on=_FHA_KEY,
            how="left",
        )
        .join(_modal(named, "fha_name"), on=_FHA_KEY, how="left")
    )
    variants = named.group_by([*_FHA_KEY, "fha_name"]).agg(variant_loans=pl.len())
    return entities, variants


# --------------------------------------------------------------------------- #
# HMDA side
# --------------------------------------------------------------------------- #


def _panel_rows(panel_dir: Path, min_year: int, max_year: int) -> pl.DataFrame:
    """Scan the HMDA panel, canonicalizing ``respondent_id`` and keeping the raw id.

    The raw id is kept because the tier-0 ID embedding reads its first five
    digits, which `hmda_respondent_key` strips.

    Returns:
        Panel rows carrying both the canonical ``resp_key`` and the raw id.
    """
    if not panel_dir.exists():
        raise FileNotFoundError(f"HMDA pre-2018 panel not found: {panel_dir}")
    return (
        pl.scan_parquet(panel_dir / "**/*.parquet", hive_partitioning=True)
        .filter(pl.col("activity_year").is_between(min_year, max_year))
        .with_columns(hmda_respondent_key())
        .filter(pl.col("resp_key").str.len_chars() > 0)
        .select(
            "activity_year",
            "respondent_id",
            "resp_key",
            "agency_code",
            pl.col("respondent_name").str.strip_chars(),
            "respondent_city",
            "respondent_state",
        )
        .collect(engine="streaming")
    )


def _hmda_fha_volume(loans_dir: Path, min_year: int, max_year: int) -> pl.DataFrame:
    """FHA originations reported per ``(resp_key, agency_code)``.

    Volume never selects a bridge entry — it orders the tier-3 audit residual and
    reports lender size. It is read here rather than left to the matcher because
    an identity asset that cannot say how big a lender is cannot be audited.

    Returns:
        One row per ``(resp_key, agency_code)``, carrying its FHA origination volume.
    """
    return (
        scan_hmda_years("period_2007_2017", min_year, max_year, root=loans_dir)
        .filter((pl.col("action_taken") == 1) & (pl.col("loan_type") == 2))
        .select(hmda_respondent_key(), "agency_code")
        .group_by(_HMDA_KEY)
        .agg(hmda_fha_loans=pl.len())
        .collect(engine="streaming")
    )


def _hmda_lender_universe(
    panel: pl.DataFrame, volume: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """HMDA respondent entities, name variants, and per-entity panel years.

    Names churn across the era (over half of entities carry more than one
    spelling), so every variant is kept for scoring and the most recent one is
    canonical. ``first_year``/``last_year`` are the *observed* panel span — the
    naming law's third pair, and what makes an era-tagged entry usable.

    Returns:
        The entities, their name variants, and per-entity observed panel years.
    """
    # Canonical identity = the one the key filed in its latest panel year. A key that
    # filed two identities in that year has no canonical one: the entity is kept with
    # its name, city and state null rather than the alphabetically first.
    latest = (
        panel.filter(pl.col("activity_year") == pl.col("activity_year").max().over(_HMDA_KEY))
        .select(*_HMDA_KEY, "respondent_name", "respondent_city", "respondent_state")
        .unique()
        .filter(pl.len().over(_HMDA_KEY) == 1)
    )
    entities = (
        panel.select(_HMDA_KEY)
        .unique()
        .join(latest, on=_HMDA_KEY, how="left", nulls_equal=True)
        .join(
            panel.group_by(_HMDA_KEY).agg(
                n_name_variants=pl.col("respondent_name").n_unique(),
                first_year=pl.col("activity_year").min().cast(pl.Int16),
                last_year=pl.col("activity_year").max().cast(pl.Int16),
            ),
            on=_HMDA_KEY,
            how="left",
        )
        .join(volume, on=_HMDA_KEY, how="left")
        .with_columns(pl.col("hmda_fha_loans").fill_null(0))
    )
    variants = (
        panel.select(*_HMDA_KEY, "respondent_name")
        .filter(pl.col("respondent_name").str.len_chars() > 0)
        .unique()
    )
    years = panel.select(*_HMDA_KEY, "activity_year").unique()
    return entities, variants, years


# --------------------------------------------------------------------------- #
# Name scoring and candidate resolution
# --------------------------------------------------------------------------- #


def _name_pair_scores(
    fha_names: pl.DataFrame, hmda_names: pl.DataFrame, floor: float, chunk: int = 1000
) -> pl.DataFrame:
    """Exhaustive token-set Jaccard over every FHA × HMDA normalized-name pair.

    No blocking. The full cross product is ~1.3e8 comparisons, which polars does
    in seconds in slices, so blocking would buy speed at a recall cost for no
    reason — first-token blocking alone would have dropped a fifth of the entries
    this route produces.

    Returns:
        One row per scoring pair at or above ``floor``.
    """
    hmda = hmda_names.with_columns(name_tokens("h_norm").alias("h_tok")).filter(
        pl.col("h_tok").list.len() > 0
    )
    out = []
    for start in range(0, fha_names.height, chunk):
        part = fha_names.slice(start, chunk).with_columns(name_tokens("f_norm").alias("f_tok"))
        out.append(
            part.filter(pl.col("f_tok").list.len() > 0)
            .join(hmda, how="cross")
            .with_columns(jaccard(pl.col("f_tok"), pl.col("h_tok")).alias("name_score"))
            .filter(pl.col("name_score") >= floor)
            .select(
                "f_norm",
                "h_norm",
                "name_score",
                (pl.col("f_norm") == pl.col("h_norm")).alias("name_exact"),
            )
        )
    return pl.concat(out, how="diagonal_relaxed")


def _pick_candidates(pool: pl.DataFrame, entity_years: pl.DataFrame, rule: str) -> pl.DataFrame:
    """Reduce a candidate pool to the entries that are defensible, or to none.

    Three outcomes per FHA lender:

    * one candidate — take it (``rule``);
    * several that never co-occur in a panel year — keep them **all**
      (``rule+era``). Pre-2018 HMDA respondent identity is not stable, so one
      institution legitimately appears under several keys in disjoint years. That
      is not ambiguity, and since the matcher blocks by year an era-tagged
      one-to-many bridge is exactly what it can consume;
    * several with overlapping years — real simultaneous ambiguity. Resolved only
      if exactly one sits in the FHA lender's modal property state
      (``rule+state``); otherwise dropped to the tier-3 residual.

    Args:
        pool: Candidate pairs to reduce.
        entity_years: Per-entity observed panel years, for the co-occurrence test.
        rule: Provenance label stamped on the entries this call produces.

    Returns:
        The defensible entries, tagged with the rule that resolved them.
    """
    counts = pool.group_by(_FHA_KEY).len().rename({"len": "n_candidates"})
    scored = pool.join(counts, on=_FHA_KEY)
    unique_pick = scored.filter(pl.col("n_candidates") == 1).with_columns(
        pl.lit(rule).alias("name_rule")
    )
    multi = scored.filter(pl.col("n_candidates") > 1)
    spans = (
        multi.join(entity_years, on=_HMDA_KEY)
        .group_by(_FHA_KEY)
        .agg(n_rows=pl.len(), n_years=pl.col("activity_year").n_unique())
    )
    disjoint = spans.filter(pl.col("n_rows") == pl.col("n_years")).select(_FHA_KEY)
    era = multi.join(disjoint, on=_FHA_KEY, how="semi").with_columns(
        pl.lit(f"{rule}+era").alias("name_rule")
    )
    same_era = multi.join(disjoint, on=_FHA_KEY, how="anti").filter(pl.col("state_agree"))
    in_state = same_era.group_by(_FHA_KEY).len().rename({"len": "n_in_state"})
    resolved = (
        same_era.join(in_state, on=_FHA_KEY)
        .filter(pl.col("n_in_state") == 1)
        .with_columns(pl.lit(f"{rule}+state").alias("name_rule"))
        .drop("n_in_state")
    )
    return pl.concat([unique_pick, era, resolved], how="diagonal_relaxed").drop("n_candidates")


def _hud_prefix_entries(
    panel: pl.DataFrame, fha_entities: pl.DataFrame, hmda_entities: pl.DataFrame
) -> pl.DataFrame:
    """Tier 0: agency-7 respondent ids whose 5-digit prefix is an FHA mortgagee.

    Read off the panel's RAW ``respondent_id`` — the canonical key strips the
    leading zeros the prefix is made of. Matched against *originating*
    mortgagees only: the embedding is a property of the FHA approval number the
    filer registered under, not of whoever sponsored a given loan.

    Returns:
        The tier-0 entries recovered from the respondent-id prefix.
    """
    originating = fha_entities.filter(pl.col("fha_id_type") == "originating")
    return (
        panel.filter(
            (pl.col("agency_code") == HUD_AGENCY_CODE)
            & pl.col("respondent_id").str.contains(_HUD_NUMERIC_ID)
        )
        .select(*_HMDA_KEY, pl.col("respondent_id").str.slice(0, 5).alias("fha_id"))
        .unique()
        .with_columns(pl.lit("originating").alias("fha_id_type"))
        .join(originating.select([*_FHA_KEY, "fha_name", "fha_state", "fha_loans"]), on=_FHA_KEY)
        .join(
            hmda_entities.select(
                [
                    *_HMDA_KEY,
                    "respondent_name",
                    "respondent_state",
                    "hmda_fha_loans",
                    "first_year",
                    "last_year",
                ]
            ),
            on=_HMDA_KEY,
        )
        .with_columns(
            jaccard(name_tokens("fha_name"), name_tokens("respondent_name")).alias("name_score"),
            pl.lit(0, dtype=pl.Int8).alias("name_tier"),
            pl.lit("hud_id_prefix").alias("name_rule"),
        )
        .with_columns((pl.col("name_score") == 1.0).alias("name_exact"))
    )


_NAMES_COLUMNS = [
    "fha_id_type",
    "fha_id",
    "fha_name",
    "fha_state",
    "fha_loans",
    "resp_key",
    "agency_code",
    "respondent_name",
    "respondent_state",
    "hmda_fha_loans",
    "first_year",
    "last_year",
    "name_tier",
    "name_rule",
    "name_score",
    "name_exact",
    "era_tagged",
]

#: Total order over the names artifact. The last four columns are the artifact's
#: unique key, so the order is total and the parquet is byte-stable across
#: rebuilds -- polars' sort alone is not stable, and a bridge that shuffles
#: between rebuilds invalidates every downstream number.
_NAMES_SORT = [
    "name_tier",
    "fha_loans",
    "fha_id_type",
    "fha_id",
    "resp_key",
    "agency_code",
]
_NAMES_SORT_DESCENDING = [False, True, False, False, False, False]


@log_execution_time
def build_name_route(
    fha_silver_dir: Path = FHA_SF_SILVER_DIR,
    hmda_panel_dir: Path = HMDA_PRE2018_PANEL_DIR,
    hmda_loans_dir: Path = HMDA_PRE2018_LOANS_DIR,
    min_year: int = PRE2018_MIN_YEAR,
    max_year: int = PRE2018_MAX_YEAR,
) -> pl.DataFrame:
    """Build the name/ID route: tiers 0-2 plus the tier-3 audit residual.

    Takes source directories and nothing else. It is not that this function
    happens not to read a crosswalk — it has no argument through which one could
    arrive, which is what makes the yield it unlocks circularity-free.

    Args:
        fha_silver_dir: FHA single-family silver dataset directory.
        hmda_panel_dir: HMDA ``period_2007_2017`` silver panel directory.
        hmda_loans_dir: HMDA ``period_2007_2017`` silver loans directory.
        min_year: First HMDA activity year to read.
        max_year: Last HMDA activity year to read.

    Returns:
        One row per ``(fha_id_type, fha_id, resp_key, agency_code)`` candidate,
        tiered; tier 3 names the best rejected candidate, or none.
    """
    first_fha_year, last_fha_year = fha_endorsement_years(min_year, max_year)
    logger.info(
        "Building the pre-2018 HMDA-FHA name route (HMDA %s-%s, FHA %s-%s)",
        min_year,
        max_year,
        first_fha_year,
        last_fha_year,
    )

    fha_rows = _fha_lender_rows(fha_silver_dir, first_fha_year, last_fha_year)
    fha_entities, fha_variants = _fha_lender_universe(fha_rows)
    _log_id_width(fha_entities)
    panel = _panel_rows(hmda_panel_dir, min_year, max_year)
    volume = _hmda_fha_volume(hmda_loans_dir, min_year, max_year)
    hmda_entities, hmda_variants, entity_years = _hmda_lender_universe(panel, volume)
    logger.info(
        "  FHA lenders %s / name variants %s | HMDA respondents %s / name variants %s",
        f"{fha_entities.height:,}",
        f"{fha_variants.height:,}",
        f"{hmda_entities.height:,}",
        f"{hmda_variants.height:,}",
    )

    fha_variants = fha_variants.with_columns(normalize_name(pl.col("fha_name")).alias("f_norm"))
    hmda_variants = hmda_variants.with_columns(
        normalize_name(pl.col("respondent_name")).alias("h_norm")
    )
    # Sorted so the chunked cross product is deterministic end to end, not just
    # in its aggregates.
    fha_names = fha_variants.select("f_norm").unique().sort("f_norm")
    hmda_names = hmda_variants.select("h_norm").unique().sort("h_norm")
    scores = _name_pair_scores(fha_names, hmda_names, JACCARD_FLOOR)
    logger.info(
        "  scored %s x %s distinct names -> %s pairs at or above %s",
        f"{fha_names.height:,}",
        f"{hmda_names.height:,}",
        f"{scores.height:,}",
        JACCARD_FLOOR,
    )

    candidates = (
        fha_variants.join(scores, on="f_norm")
        .join(hmda_variants, on="h_norm")
        .group_by([*_FHA_KEY, *_HMDA_KEY])
        .agg(
            name_score=pl.col("name_score").max(),
            name_exact=pl.col("name_exact").any(),
        )
        .join(fha_entities.select([*_FHA_KEY, "fha_name", "fha_state", "fha_loans"]), on=_FHA_KEY)
        .join(
            hmda_entities.select(
                [
                    *_HMDA_KEY,
                    "respondent_name",
                    "respondent_state",
                    "hmda_fha_loans",
                    "first_year",
                    "last_year",
                ]
            ),
            on=_HMDA_KEY,
        )
        .with_columns((pl.col("fha_state") == pl.col("respondent_state")).alias("state_agree"))
    )

    tier0 = _hud_prefix_entries(panel, fha_entities, hmda_entities)
    exact_pool = candidates.filter(pl.col("name_exact"))
    tier1 = _pick_candidates(exact_pool, entity_years, "exact").with_columns(
        pl.lit(1, dtype=pl.Int8).alias("name_tier")
    )
    # An FHA lender with any exact-name candidate is never scored on Jaccard:
    # the weaker rule must not reopen a decision the stronger one already made.
    no_exact = candidates.join(exact_pool.select(_FHA_KEY).unique(), on=_FHA_KEY, how="anti")
    jaccard_pool = no_exact.filter(
        (pl.col("name_score") >= JACCARD_THRESHOLD)
        & (pl.col("name_score") == pl.col("name_score").max().over(_FHA_KEY))
    )
    tier2 = _pick_candidates(jaccard_pool, entity_years, "jaccard").with_columns(
        pl.lit(2, dtype=pl.Int8).alias("name_tier")
    )
    resolved = pl.concat([tier0, tier1, tier2], how="diagonal_relaxed")
    tier3 = _audit_residual(fha_entities, candidates, resolved)

    route = (
        pl.concat([resolved, tier3], how="diagonal_relaxed")
        .with_columns(pl.col("name_rule").str.ends_with("+era").alias("era_tagged"))
        .sort(_NAMES_SORT, descending=_NAMES_SORT_DESCENDING)
        # A lender reached by the deterministic route in one era is often reached
        # by name in another; the strongest tier wins the row. Each tier's pool is
        # unique per (lender, respondent) and tiers 1-3 are lender-disjoint, so rows
        # sharing a pair differ in name_tier alone.
        # tie-safe: a pair's rows differ in name_tier, so the first is its unique best tier
        .unique(subset=[*_FHA_KEY, *_HMDA_KEY], keep="first", maintain_order=True)
        .select(_NAMES_COLUMNS)
    )
    _log_tiers(route, fha_entities)
    return route


def _audit_residual(
    fha_entities: pl.DataFrame, candidates: pl.DataFrame, resolved: pl.DataFrame
) -> pl.DataFrame:
    """Tier 3: FHA lenders no rule resolved, carrying their best rejected candidate.

    Not a bridge entry — it exists so the misses are readable, and it is filtered
    out of the merged artifact. ``no_candidate`` means the lender scored nothing
    at all above `JACCARD_FLOOR`.

    Returns:
        One row per unresolved FHA lender, carrying its best rejected candidate.
    """
    rest = fha_entities.join(resolved.select(_FHA_KEY).unique(), on=_FHA_KEY, how="anti")
    # The best rejected candidate: highest score, then largest HMDA FHA volume. When
    # several tie on both, the lender keeps the shared score (so its rule still reads)
    # but names no candidate -- choosing one by respondent id would be arbitrary.
    top = candidates.join(rest.select(_FHA_KEY), on=_FHA_KEY, how="semi")
    for col in ("name_score", "hmda_fha_loans"):
        top = top.filter(pl.col(col) == pl.col(col).max().over(_FHA_KEY))
    best = pl.concat(
        [
            top.filter(pl.len().over(_FHA_KEY) == 1),
            top.filter(pl.len().over(_FHA_KEY) > 1)
            .group_by(_FHA_KEY)
            .agg(pl.col("name_score").max(), pl.col("name_exact").any()),
        ],
        how="diagonal_relaxed",
    )
    return (
        rest.select([*_FHA_KEY, "fha_name", "fha_state", "fha_loans"])
        .join(best.drop("fha_name", "fha_state", "fha_loans"), on=_FHA_KEY, how="left")
        .with_columns(
            pl.lit(3, dtype=pl.Int8).alias("name_tier"),
            pl.when(pl.col("name_score").is_null())
            .then(pl.lit("no_candidate"))
            .when(pl.col("name_exact"))
            .then(pl.lit("exact_ambiguous"))
            .when(pl.col("name_score") >= JACCARD_THRESHOLD)
            .then(pl.lit("jaccard_ambiguous"))
            .otherwise(pl.lit("below_threshold"))
            .alias("name_rule"),
        )
    )


def _log_id_width(fha_entities: pl.DataFrame) -> None:
    """Warn about FHA lender ids that are not silver's guaranteed 5 characters."""
    odd = fha_entities.filter(pl.col("fha_id").str.len_chars() != 5)
    if odd.is_empty():
        return
    logger.warning(
        "%s FHA lender id(s) are not 5 characters and cannot join: %s",
        f"{odd.height:,}",
        odd.select(_FHA_KEY).rows(),
    )


def _log_tiers(route: pl.DataFrame, fha_entities: pl.DataFrame) -> None:
    """Log the tier/rule composition and its share of FHA endorsement volume."""
    total = fha_entities["fha_loans"].sum()
    summary = (
        route.group_by("name_tier", "name_rule")
        .agg(rows=pl.len())
        .join(
            # fha_loans is constant per lender, so this collapses identical rows.
            route.select("name_tier", "name_rule", *_FHA_KEY, "fha_loans")
            .unique()
            .group_by("name_tier", "name_rule")
            .agg(lenders=pl.len(), loans=pl.col("fha_loans").sum()),
            on=["name_tier", "name_rule"],
        )
        .sort(["name_tier", "loans"], descending=[False, True])
    )
    for row in summary.iter_rows(named=True):
        logger.info(
            "    tier %s %-18s rows %8s  lenders %7s  FHA loans %11s (%.1f%%)",
            row["name_tier"],
            row["name_rule"],
            f"{row['rows']:,}",
            f"{row['lenders']:,}",
            f"{row['loans']:,}",
            row["loans"] / total * 100,
        )


# --------------------------------------------------------------------------- #
# The bridge proper
# --------------------------------------------------------------------------- #

_PROVENANCE = {
    (True, True): "agree",
    (True, False): "name_only",
    (False, True): "learned_only",
}

_BRIDGE_COLUMNS = [
    "fha_id_type",
    "fha_id",
    "fha_name",
    "resp_key",
    "agency_code",
    "respondent_name",
    "first_year",
    "last_year",
    "era_tagged",
    "name_tier",
    "name_rule",
    "name_score",
    "fha_loans",
    "hmda_fha_loans",
    "in_name_route",
    "in_learned_route",
    "provenance",
    "merge_rule",
]


def _check_provenance(bridge: pl.DataFrame) -> None:
    """Raise unless every row resolves to exactly one route and one provenance.

    Guards against a ``route``-style column that records which row survived
    deduplication rather than where the evidence came from. Build time is the only
    place this can be caught, so it raises rather than logs.

    Raises:
        ValueError: If a route flag or ``provenance`` is null, if a row belongs
            to no route, or if ``provenance`` disagrees with the route flags.
    """
    expected = (
        pl.when(pl.col("in_name_route") & pl.col("in_learned_route"))
        .then(pl.lit("agree"))
        .when(pl.col("in_name_route"))
        .then(pl.lit("name_only"))
        .when(pl.col("in_learned_route"))
        .then(pl.lit("learned_only"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )
    bad = bridge.select(
        null_flags=(pl.col("in_name_route").is_null() | pl.col("in_learned_route").is_null()).sum(),
        no_route=(~(pl.col("in_name_route") | pl.col("in_learned_route"))).sum(),
        mismatched=(pl.col("provenance") != expected).sum(),
        null_provenance=pl.col("provenance").is_null().sum(),
    ).row(0, named=True)
    if any(bad.values()):
        raise ValueError(f"bridge provenance invariant violated: {bad}")


@log_execution_time
def build_bridge(
    name_route: pl.DataFrame | None = None,
    fha_silver_dir: Path = FHA_SF_SILVER_DIR,
    hmda_panel_dir: Path = HMDA_PRE2018_PANEL_DIR,
    hmda_loans_dir: Path = HMDA_PRE2018_LOANS_DIR,
    min_year: int = PRE2018_MIN_YEAR,
    max_year: int = PRE2018_MAX_YEAR,
) -> pl.DataFrame:
    """Assemble the bridge from the name route's tier 0-2 entries.

    Tier 3 is the audit residual, names no respondent, and is dropped here. Route
    provenance is written even though only one route exists, and checked by
    `_check_provenance`: a learned route lands by unioning its entries and
    setting ``in_learned_route``, with no schema change.

    Args:
        name_route: Output of `build_name_route`; rebuilt if omitted.
        fha_silver_dir: FHA single-family silver dataset directory.
        hmda_panel_dir: HMDA ``period_2007_2017`` silver panel directory.
        hmda_loans_dir: HMDA ``period_2007_2017`` silver loans directory.
        min_year: First HMDA activity year to read when rebuilding.
        max_year: Last HMDA activity year to read when rebuilding.

    Returns:
        One row per ``(fha_id_type, fha_id, resp_key, agency_code)``.
    """
    if name_route is None:
        name_route = build_name_route(
            fha_silver_dir, hmda_panel_dir, hmda_loans_dir, min_year, max_year
        )
    bridge = (
        name_route.filter(pl.col("name_tier") <= 2)
        .with_columns(
            pl.lit(value=True).alias("in_name_route"),
            pl.lit(value=False).alias("in_learned_route"),
            pl.lit(_PROVENANCE[True, False]).alias("provenance"),
            pl.lit("name_only").alias("merge_rule"),
        )
        .select(_BRIDGE_COLUMNS)
    )
    _check_provenance(bridge)
    logger.info(
        "  bridge entries %s over %s FHA lenders and %s HMDA respondents (%s era-tagged)",
        f"{bridge.height:,}",
        f"{bridge.select(_FHA_KEY).unique().height:,}",
        f"{bridge.select(_HMDA_KEY).unique().height:,}",
        f"{int(bridge['era_tagged'].sum()):,}",
    )
    return bridge


@log_execution_time
def run_build(
    min_year: int = PRE2018_MIN_YEAR,
    max_year: int = PRE2018_MAX_YEAR,
    overwrite: bool = False,
    fha_silver_dir: Path = FHA_SF_SILVER_DIR,
    hmda_panel_dir: Path = HMDA_PRE2018_PANEL_DIR,
    hmda_loans_dir: Path = HMDA_PRE2018_LOANS_DIR,
) -> dict[str, int]:
    """Build and write both bridge artifacts.

    Args:
        min_year: First HMDA activity year to read.
        max_year: Last HMDA activity year to read.
        overwrite: Rebuild artifacts that already exist.
        fha_silver_dir: FHA single-family silver dataset directory.
        hmda_panel_dir: HMDA ``period_2007_2017`` silver panel directory.
        hmda_loans_dir: HMDA ``period_2007_2017`` silver loans directory.

    Returns:
        Row counts keyed by artifact stem (zero when the build was skipped).
    """
    names_path = HMDAFHAMatchingConfig.get_bridge_path(NAMES_ROUTE_NAME)
    bridge_path = HMDAFHAMatchingConfig.get_bridge_path(BRIDGE_NAME)
    if not should_process_file(names_path, overwrite) and not should_process_file(
        bridge_path, overwrite
    ):
        logger.info("Both bridge artifacts exist; pass overwrite=True to rebuild")
        return {NAMES_ROUTE_NAME: 0, BRIDGE_NAME: 0}

    names_path.parent.mkdir(parents=True, exist_ok=True)
    route = build_name_route(fha_silver_dir, hmda_panel_dir, hmda_loans_dir, min_year, max_year)
    route.write_parquet(names_path)
    logger.info("Wrote %s (%s rows)", names_path, f"{route.height:,}")

    bridge = build_bridge(route)
    bridge.write_parquet(bridge_path)
    logger.info("Wrote %s (%s rows)", bridge_path, f"{bridge.height:,}")
    return {NAMES_ROUTE_NAME: route.height, BRIDGE_NAME: bridge.height}


def load_bridge(route: Literal["merged", "name"] = "merged") -> pl.DataFrame:
    """Load a built bridge artifact.

    Args:
        route: ``"merged"`` for the bridge proper (tiers 0-2, with provenance),
            ``"name"`` for the whole name route including the tier-3 residual.
            A ``"learned"`` option lands with the learned route.

    Returns:
        The requested bridge artifact.

    Raises:
        FileNotFoundError: If the artifact has not been built yet.
    """
    name = BRIDGE_NAME if route == "merged" else NAMES_ROUTE_NAME
    path = HMDAFHAMatchingConfig.get_bridge_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"Pre-2018 HMDA-FHA lender bridge not built: {path}. "
            "Run `mortgage-data match hmda-fha run --era pre2018 --step bridge`."
        )
    return pl.read_parquet(path)
