"""Paths for the HMDA-FHFA matching workflow (the post-2018 rounds live in `matching_post2018`)."""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.matching.config import MatchingConfig


class HMDAFHFAMatchingConfig(MatchingConfig):
    """HMDA-FHFA matching configuration, inheriting from MatchingConfig."""

    # HMDA-FHFA specific data directory
    HMDA_FHFA_MATCHING_DIR: Path = Path(
        config(
            "HMDA_FHFA_MATCHING_DIR",
            default=str(MatchingConfig.get_matching_type_dir("hmda_fhfa")),
        )
    )

    # Output directories for matching results
    HMDA_FHFA_OUTPUT_DIR: Path = Path(
        config(
            "HMDA_FHFA_OUTPUT_DIR",
            default=str(HMDA_FHFA_MATCHING_DIR / "output"),
        )
    )

    # Crosswalk output directory (crosswalk/hmda_fhfa/)
    HMDA_FHFA_CROSSWALK_DIR: Path = Path(
        config(
            "HMDA_FHFA_CROSSWALK_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("hmda_fhfa")),
        )
    )

    #: Each era's full year range. A run over exactly this range writes the era's
    #: crosswalk; any other range writes a range-suffixed file beside it.
    ERA_YEARS: dict[str, tuple[int, int]] = {"pre2018": (2008, 2017), "post2018": (2018, 2025)}

    @classmethod
    def get_crosswalk_path(cls, era: str, years: list[int] | None = None) -> Path:
        """The final crosswalk for one era.

        Args:
            era: ``"post2018"`` or ``"pre2018"``.
            years: The years a run covered; None or the era's full range addresses the
                era's crosswalk, any other range a ``..._{min}_{max}`` file beside it, so
                a partial run never overwrites the full one.

        Returns:
            ``crosswalk/hmda_fhfa/hmda_fhfa_crosswalk_{era}[_{min}_{max}].parquet``.
        """
        name = f"hmda_fhfa_crosswalk_{era}"
        if years and (min(years), max(years)) != cls.ERA_YEARS[era]:
            name += f"_{min(years)}_{max(years)}"
        return cls.HMDA_FHFA_CROSSWALK_DIR / f"{name}.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA-FHFA matching directories if they don't exist."""
        for d in [
            cls.HMDA_FHFA_MATCHING_DIR,
            cls.HMDA_FHFA_OUTPUT_DIR,
            cls.HMDA_FHFA_CROSSWALK_DIR,
        ]:
            d.mkdir(parents=True, exist_ok=True)


__all__ = ["HMDAFHFAMatchingConfig"]
