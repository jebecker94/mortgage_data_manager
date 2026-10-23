"""Configuration settings for MBS-UMBS matching.

This module provides configuration for matching MBS loan-level data to UMBS
issuance data for FNMA and FHLMC.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
from mortgage_data_manager.sources.fnma.config import FNMAConfig


class MBSUMBSMatchingConfig(MatchingConfig):
    """Configuration for MBS-UMBS matching workflow."""

    # Matching directories
    MBS_UMBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("mbs_umbs")
    MBS_UMBS_OUTPUT_DIR: Path = MBS_UMBS_MATCHING_DIR / "output"
    MBS_UMBS_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("mbs_umbs")

    # UMBS bronze data directory
    UMBS_BRONZE_DIR: Path = MortgageDataConfig.get_subpackage_data_dir("umbs") / "bronze"
    UMBS_SILVER_DIR: Path = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"

    @classmethod
    def get_fhlmc_config(cls) -> type[FHLMCConfig]:
        """The FHLMC config class, source of the Freddie loan-level paths."""
        return FHLMCConfig

    @classmethod
    def get_fnma_config(cls) -> type[FNMAConfig]:
        """The FNMA config class, source of the Fannie loan-level paths."""
        return FNMAConfig

    @classmethod
    def get_umbs_bronze_dir(cls) -> Path:
        """Get UMBS bronze directory path.

        Returns:
            Path to the UMBS bronze data directory.
            UMBS bronze data is organized as: bronze/{GSE}/{folder}/
            For FHLMC UMBS data: bronze/FHLMC/FRE_ILLD/
        """
        return cls.UMBS_BRONZE_DIR

    @classmethod
    def get_mbs_dir(cls, gse: str) -> Path:
        """Get the GSE single-family loan-level silver directory — the MBS side of the match.

        Args:
            gse: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            Path to the GSE's silver loan-level directory (``issuances`` for Fannie,
            ``origination`` for Freddie).

        Raises:
            ValueError: If ``gse`` is neither ``"fnma"`` nor ``"fhlmc"``.
        """
        key = gse.lower()
        if key == "fnma":
            return MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
        if key == "fhlmc":
            return cls.get_fhlmc_config().FHLMC_SILVER_DIR / "origination"
        raise ValueError(f"unknown GSE {gse!r}; expected 'fnma' or 'fhlmc'")

    @classmethod
    def get_mbs_harp_file(cls, gse: str) -> Path | None:
        """Get the GSE's HARP/Refi Plus issuance file, the second half of the MBS side.

        Fannie publishes its HARP population as a separate release rather than inside the
        quarterly single-family files, so ``get_mbs_dir`` reaches only part of its book:
        the two loan-id sets are disjoint, and every HARP loan is a no-cash-out refinance,
        which is exactly the purpose the UMBS side leaves unmatched pre-2019. The silver
        layout is column- and dtype-identical to ``silver/issuances``, so the file joins
        the MBS universe with no remapping. Freddie has no equivalent release — its relief
        refinances sit in the standard files — so this is ``None`` for ``"fhlmc"``.

        Args:
            gse: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            Path to the HARP issuance parquet, or ``None`` when the GSE has no such
            release or the file has not been built.
        """
        if gse.lower() != "fnma":
            return None
        path = (
            MortgageDataConfig.get_subpackage_data_dir("fnma")
            / "silver"
            / "harp"
            / "issuances.parquet"
        )
        return path if path.exists() else None

    @classmethod
    def get_umbs_illd_dir(cls, gse: str) -> Path:
        """Get the UMBS issuance-disclosure (ILLD) directory for a GSE.

        Reads **silver**, not bronze. Silver merges each month's original with its
        R-prefixed reissue (see ``umbs.config.CORRECTION_PAIRS``), conforms every
        disclosure-format vintage to one schema, and null-maps the score/ratio
        sentinels. Bronze folder names are per-GSE (``FRE_ILLD``/``FNM_ILLD``);
        silver uses the canonical kind name for both.

        Args:
            gse: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            Path to the GSE's ILLD silver directory.
        """
        return cls.UMBS_SILVER_DIR / gse.upper() / "ILLD"

    # The first month of UMBS issuance disclosure (ILLD). Loans originated before this never
    # appear in ILLD, but those still outstanding on this date appear in the first MONTHLY
    # snapshot (MLLD / FU) — see get_umbs_snapshot_file.
    UMBS_ISSUANCE_START: str = "2019-06-01"

    # First monthly loan-level snapshot kind per GSE (the pre-2019 recovery source), as
    # named in the SILVER layout.
    _SNAPSHOT_FOLDER: dict[str, str] = {"fnma": "FNMA/MLLD", "fhlmc": "FHLMC/FU"}

    @classmethod
    def get_umbs_snapshot_file(cls, gse: str) -> Path | None:
        """Get the first monthly UMBS snapshot file for a GSE (pre-2019 recovery source).

        The first monthly loan-level disclosure file (``MLLD_201906`` for Fannie,
        ``FU_201906`` for Freddie) is a snapshot of every loan outstanding in a
        UMBS-eligible pool as of June 2019. It carries the same at-origination static fields
        as ILLD, so it recovers loans issued before ILLD coverage began
        (`UMBS_ISSUANCE_START`).

        Reads **silver**. The silver filenames sort chronologically (``FU_201906`` <
        ``FU_201907``); the bronze ones did too, but under a different convention
        (``fu190606``), so this must not be repointed at bronze without re-checking that
        ``sorted(...)[0]`` still yields June 2019.

        Args:
            gse: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            Path to the earliest monthly snapshot parquet, or ``None`` if the folder is
            absent or empty.
        """
        folder = cls._SNAPSHOT_FOLDER.get(gse.lower())
        if folder is None:
            return None
        snapshot_dir = cls.UMBS_SILVER_DIR / folder
        if not snapshot_dir.exists():
            return None
        files = sorted(snapshot_dir.glob("*.parquet"))
        return files[0] if files else None

    @classmethod
    def get_crosswalk_path(cls, agency: str) -> Path:
        """The final crosswalk for one GSE; its metrics sidecar sits beside it.

        Args:
            agency: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            ``crosswalk/mbs_umbs/mbs_umbs_crosswalk_{agency}.parquet``.
        """
        return cls.MBS_UMBS_CROSSWALK_DIR / f"mbs_umbs_crosswalk_{agency.lower()}.parquet"

    @classmethod
    def get_column_mapping_file(cls, agency: str) -> Path:
        """The CSV mapping one GSE's MBS and UMBS columns onto standardized names.

        Args:
            agency: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            ``schemas/umbs/{agency}_column_mapping.csv``.
        """
        return cls.SCHEMAS_DIR / "umbs" / f"{agency.lower()}_column_mapping.csv"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary MBS-UMBS matching directories if they don't exist."""
        cls.MBS_UMBS_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_UMBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_UMBS_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
