"""Configuration for the master crosswalk (the HMDA spine).

The master crosswalk consumes the crosswalks every other ``matching/`` workflow
produces, so this config is mostly an inventory of those inputs plus the edge
registry that drives the assembly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NamedTuple

from decouple import config

from mortgage_data_manager.matching.config import MatchingConfig


class EdgeSpec(NamedTuple):
    """One input edge of the spine.

    Attributes:
        name: Short edge name; also the ``in_{name}`` flag stem.
        workflow: The ``matching/`` workflow that produces it.
        required: If True, a missing artifact aborts the build. Optional edges
            are skipped with a warning so a partial input set still yields a
            usable spine (the QC records which edges were present).
    """

    name: str
    workflow: str
    required: bool


class MasterCrosswalkConfig(MatchingConfig):
    """Configuration for the master crosswalk workflow."""

    # --- output layout -------------------------------------------------------
    # Final artifact -> crosswalk/master/ (hive by activity_year), staging and
    # QC results -> data/matching/master/ per the intermediate/final split.
    MASTER_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("master")
    MASTER_STAGING_DIR: Path = MASTER_MATCHING_DIR / "staging"
    MASTER_QC_DIR: Path = MASTER_MATCHING_DIR / "qc"
    MASTER_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("master")

    #: Column the artifact is hive-partitioned by.
    PARTITION_COL: str = "activity_year"

    # --- requested year range ------------------------------------------------
    # HMDA LEI (and so HMDAIndex) begins 2018. The upper bound is the latest
    # HMDA activity year with ANY edge, not the latest with every edge --
    # conventional edges stop earlier (see CONVENTIONAL_MAX_YEAR).
    MIN_YEAR: int = int(config("MASTER_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("MASTER_MAX_YEAR", default="2025"))

    #: Last year reachable by the conventional (GSE) edges. FHFA PUDB and
    #: ``combined/loan_issuance`` GSE coverage both end here, so later years are
    #: right-censored: a conventional loan appears only if seller/purchaser
    #: matched, and then as ``channel='other'``. Advance when both land.
    CONVENTIONAL_MAX_YEAR: int = int(config("MASTER_CONVENTIONAL_MAX_YEAR", default="2025"))

    # --- input crosswalks ----------------------------------------------------
    HMDA_FHA_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fha") / (
        "hmda_fha_crosswalk_post2018.parquet"
    )
    #: The pre-2018 HMDA-FHA crosswalk. Not an edge -- the spine starts at 2018 --
    #: but its FHA side feeds the ``fha_cross_era`` conflict: both eras' matchers
    #: can reach late-2017 / January-2018 endorsements, so one endorsement can be
    #: paired with a 2017 loan there and a later loan in the edge.
    HMDA_FHA_PRE2018_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fha") / (
        "hmda_fha_crosswalk_pre2018.parquet"
    )
    HMDA_FHFA_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fhfa") / (
        "hmda_fhfa_crosswalk_post2018.parquet"
    )
    HMDA_FHLB_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fhlb") / (
        "hmda_fhlb_crosswalk_post2018.parquet"
    )
    FHLB_MBS_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("fhlb_mbs") / (
        "fhlb_mbs_crosswalk_gnma.parquet"
    )
    HMDA_SELLERS_PURCHASERS_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir(
        "hmda_sellers_purchasers"
    ) / ("hmda_sellers_purchasers_crosswalk_post2018")
    HMDA_GNMA_FHA_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_gnma") / (
        "hmda_gnma_crosswalk_fha.parquet"
    )
    HMDA_GNMA_VA_USDA_CROSSWALK: Path = MatchingConfig.get_crosswalk_type_dir("hmda_gnma") / (
        "hmda_gnma_crosswalk_va_usda.parquet"
    )

    #: GSE chain masters (HMDA -> FHFA -> MBS -> UMBS), one per enterprise.
    GSES: tuple[str, ...] = ("fnma", "fhlmc")

    # --- assembly constants --------------------------------------------------
    # (The direct edge's admission rule is `match_hmda_gse.matching_direct.admitted_row`.)

    # --- seller/purchaser ownership chain ------------------------------------
    #: HMDA silver, needed for each purchase record's OWN ``purchaser_type`` --
    #: the signal that orders successive owners. This is the workflow's only
    #: non-crosswalk input.
    HMDA_SILVER_LOANS_DIR: Path = Path(
        config(
            "HMDA_SILVER_LOANS_DIR",
            default=str(
                MatchingConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"
            ),
        )
    )

    #: ``action_taken`` for a purchased-loan record.
    PURCHASED_LOAN_ACTION: int = 6

    #: ``purchaser_type`` values naming an agency buyer (Fannie / Ginnie / Freddie).
    #: A purchase record reporting one of these sold the loan ON to an agency, so it
    #: is the LAST HMDA-visible hop -- the agencies are not HMDA reporters and file
    #: no purchase records of their own, which is why the terminal agency hop comes
    #: from the conventional/GNMA edges instead of this one.
    AGENCY_PURCHASER_TYPES: tuple[int, ...] = (1, 2, 3)

    #: Edge inventory, in join order. Drives the flags, the QC coverage table,
    #: and the "which edges were present" record.
    EDGES: tuple[EdgeSpec, ...] = (
        EdgeSpec("fha", "match_hmda_fha", required=True),
        EdgeSpec("fhfa", "match_hmda_fhfa", required=True),
        EdgeSpec("mbs", "match_hmda_gse", required=True),
        EdgeSpec("umbs", "match_hmda_gse", required=True),
        EdgeSpec("gnma", "match_hmda_gnma", required=True),
        EdgeSpec("fhlb", "match_hmda_fhlb", required=False),
        EdgeSpec("seller_purchaser", "match_hmda_sellers_purchasers", required=True),
    )

    @classmethod
    def flag_columns(cls) -> list[str]:
        """The ``in_*`` coverage flags, in edge order.

        Returns:
            The coverage-flag column names, in edge order.
        """
        return [f"in_{e.name}" for e in cls.EDGES]

    @classmethod
    def get_chain_crosswalk_path(cls, gse: Literal["fnma", "fhlmc"]) -> Path:
        """Path to a GSE chain master crosswalk.

        Args:
            gse: Which GSE's chain master to address.

        Returns:
            The chain master crosswalk path.
        """
        return cls.get_crosswalk_type_dir("hmda_gse") / f"hmda_gse_crosswalk_chain_{gse}.parquet"

    @classmethod
    def get_direct_crosswalk_path(cls, year: int) -> Path:
        """Path to one year of the direct HMDA<->issuance crosswalk.

        Args:
            year: HMDA activity year.

        Returns:
            The direct-issuance crosswalk path for that year.
        """
        return cls.get_crosswalk_type_dir("hmda_gse") / f"hmda_gse_crosswalk_direct_{year}.parquet"

    @classmethod
    def get_mbs_umbs_path(cls, gse: Literal["fnma", "fhlmc"]) -> Path:
        """Path to a GSE MBS->UMBS crosswalk (fills ``umbs_loan_id`` for direct rows).

        Args:
            gse: Which GSE's crosswalk to address.

        Returns:
            The MBS->UMBS crosswalk path.
        """
        return cls.get_crosswalk_type_dir("mbs_umbs") / f"mbs_umbs_crosswalk_{gse}.parquet"

    @classmethod
    def get_partition_dir(cls, year: int, *, staging: bool = False) -> Path:
        """Hive partition directory for one activity year.

        Args:
            year: HMDA activity year.
            staging: Address the private staging tree instead of the crosswalk.

        Returns:
            The hive partition directory for that year.
        """
        base = cls.MASTER_STAGING_DIR if staging else cls.MASTER_CROSSWALK_DIR
        return base / f"{cls.PARTITION_COL}={year}"

    @classmethod
    def get_qc_path(cls, stamp: str) -> Path:
        """Path to a QC result JSON for a given run stamp.

        Args:
            stamp: Run stamp identifying the QC output.

        Returns:
            The QC result JSON path.
        """
        return cls.MASTER_QC_DIR / f"master_qc_{stamp}.json"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create the workflow's own directories if they don't exist."""
        cls.MASTER_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MASTER_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MASTER_QC_DIR.mkdir(parents=True, exist_ok=True)
        cls.MASTER_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
