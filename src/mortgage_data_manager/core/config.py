"""Base configuration for mortgage data manager.

This module provides the base MortgageDataConfig class that all subpackage
configurations inherit from. It handles path resolution, environment variable
management, and common directory operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from decouple import config


def _default_schemas_dir(project_dir: Path) -> str:
    """Resolve the default ``schemas/`` reference-data root for dev and wheel installs.

    Source tree / editable install: ``<project>/schemas`` (alongside the repo). Built
    wheel: the packaged copy at ``mortgage_data_manager/_schemas`` (shipped via the hatch
    build hook in pyproject). An explicit ``MORTGAGE_DATA_SCHEMAS_DIR`` env var overrides both.
    """
    repo_schemas = project_dir / 'schemas'
    if repo_schemas.is_dir():
        return str(repo_schemas)
    packaged = Path(__file__).resolve().parents[1] / '_schemas'
    if packaged.is_dir():
        return str(packaged)
    return str(repo_schemas)


class MortgageDataConfig:
    """Base configuration for mortgage data manager.

    This class provides common configuration and path management for all
    subpackages. Subpackages should inherit from this class and add their
    own specific configuration.

    Environment Variables:
        MORTGAGE_DATA_PROJECT_DIR: Root project directory
        MORTGAGE_DATA_DIR: Root data directory
        MORTGAGE_OUTPUT_DIR: Root output directory
        MORTGAGE_DATA_USER_AGENT: HTTP User-Agent header for web requests
        DOWNLOAD_RETRIES: Number of retry attempts for downloads (default: 3)
        DOWNLOAD_TIMEOUT: Request timeout in seconds (default: 30)
        DOWNLOAD_PAUSE: Pause between batch downloads in seconds (default: 5.0)
        DOWNLOAD_CHUNK_SIZE: Chunk size for streaming downloads (default: 8192)
        DOWNLOAD_BACKOFF_FACTOR: Exponential backoff multiplier (default: 2.0)

    Example:
        >>> from mortgage_data_manager.core.config import MortgageDataConfig
        >>> config = MortgageDataConfig()
        >>> print(config.PROJECT_DIR)
        /Users/username/mortgage_data_manager
    """

    # Default user agent for HTTP requests. The Chrome-mimicking default is
    # deliberate: some scraped sources treat bot-style user agents differently.
    USER_AGENT: str = config(
        'MORTGAGE_DATA_USER_AGENT',
        default='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )

    # Download configuration
    DOWNLOAD_RETRIES: int = int(config('DOWNLOAD_RETRIES', default='3'))
    DOWNLOAD_TIMEOUT: int = int(config('DOWNLOAD_TIMEOUT', default='30'))
    DOWNLOAD_PAUSE: float = float(config('DOWNLOAD_PAUSE', default='5.0'))
    DOWNLOAD_CHUNK_SIZE: int = int(config('DOWNLOAD_CHUNK_SIZE', default='8192'))
    DOWNLOAD_BACKOFF_FACTOR: float = float(config('DOWNLOAD_BACKOFF_FACTOR', default='2.0'))

    # Root directories
    PROJECT_DIR: Path = Path(
        config(
            'MORTGAGE_DATA_PROJECT_DIR',
            default=str(Path(__file__).resolve().parent.parent.parent.parent)
        )
    )

    DATA_DIR: Path = Path(
        config(
            'MORTGAGE_DATA_DIR',
            default=str(PROJECT_DIR / 'data')
        )
    )

    OUTPUT_DIR: Path = Path(
        config(
            'MORTGAGE_OUTPUT_DIR',
            default=str(PROJECT_DIR / 'output')
        )
    )

    SCHEMAS_DIR: Path = Path(
        config(
            'MORTGAGE_DATA_SCHEMAS_DIR',
            default=_default_schemas_dir(PROJECT_DIR)
        )
    )

    CROSSWALK_DIR: Path = Path(
        config(
            'MORTGAGE_DATA_CROSSWALK_DIR',
            default=str(PROJECT_DIR / 'crosswalk')
        )
    )

    # Private (proprietary, non-public) data roots. These mirror DATA_DIR and
    # CROSSWALK_DIR but hold data/outputs that must never be committed — this
    # repository has a public remote. They default inside a gitignored
    # ``private/`` folder as a safety net, but are meant to be overridden to an
    # EXTERNAL location (e.g. a private volume) via the env vars below.
    PRIVATE_DATA_DIR: Path = Path(
        config(
            'MORTGAGE_PRIVATE_DATA_DIR',
            default=str(PROJECT_DIR / 'private' / 'data')
        )
    )

    PRIVATE_CROSSWALK_DIR: Path = Path(
        config(
            'MORTGAGE_PRIVATE_CROSSWALK_DIR',
            default=str(PROJECT_DIR / 'private' / 'crosswalk')
        )
    )

    @classmethod
    def get_subpackage_crosswalk_dir(cls, subpackage: str) -> Path:
        """Get crosswalk directory for a specific subpackage.

        Args:
            subpackage: Name of the subpackage (e.g., 'fha_gnma', 'mbs_umbs')

        Returns:
            Path to the subpackage's crosswalk directory
        """
        return cls.CROSSWALK_DIR / subpackage

    @classmethod
    def get_subpackage_schemas_dir(cls, subpackage: str) -> Path:
        """Get schemas directory for a specific subpackage.

        Args:
            subpackage: Name of the subpackage (e.g., 'gnma', 'fhfa')

        Returns:
            Path to the subpackage's schemas directory

        Example:
            >>> MortgageDataConfig.get_subpackage_schemas_dir('gnma')
            PosixPath('/Users/username/mortgage_data_manager/schemas/gnma')
        """
        return cls.SCHEMAS_DIR / subpackage

    @classmethod
    def get_subpackage_data_dir(cls, subpackage: str) -> Path:
        """Get data directory for a specific subpackage.

        Args:
            subpackage: Name of the subpackage (e.g., 'hmda', 'fha', 'mbs')

        Returns:
            Path to the subpackage's data directory

        Example:
            >>> MortgageDataConfig.get_subpackage_data_dir('hmda')
            PosixPath('/Users/username/mortgage_data_manager/data/hmda')
        """
        return cls.DATA_DIR / subpackage

    @classmethod
    def get_medallion_dir(
        cls,
        subpackage: str,
        stage: Literal["raw", "bronze", "silver", "gold"],
    ) -> Path:
        """Get medallion stage directory for a subpackage.

        Args:
            subpackage: Name of the subpackage (e.g., 'hmda', 'fha', 'mbs')
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')

        Returns:
            Path to the medallion stage directory

        Example:
            >>> MortgageDataConfig.get_medallion_dir('hmda', 'silver')
            PosixPath('/Users/username/mortgage_data_manager/data/hmda/silver')
        """
        return cls.get_subpackage_data_dir(subpackage) / stage

    @classmethod
    def get_private_subpackage_data_dir(cls, subpackage: str) -> Path:
        """Get the data directory for a proprietary (non-public) subpackage.

        Mirrors :meth:`get_subpackage_data_dir` but roots under
        ``PRIVATE_DATA_DIR`` so proprietary data lives outside the public
        repository. Override ``MORTGAGE_PRIVATE_DATA_DIR`` to an external path.

        Args:
            subpackage: Name of the proprietary subpackage (e.g. 'vendor_source')

        Returns:
            Path to the subpackage's private data directory
        """
        return cls.PRIVATE_DATA_DIR / subpackage

    @classmethod
    def get_private_medallion_dir(
        cls,
        subpackage: str,
        stage: Literal["raw", "bronze", "silver", "gold"],
    ) -> Path:
        """Get the medallion stage directory for a proprietary subpackage.

        Args:
            subpackage: Name of the proprietary subpackage (e.g. 'vendor_source')
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')

        Returns:
            Path to the private medallion stage directory
        """
        return cls.get_private_subpackage_data_dir(subpackage) / stage

    @classmethod
    def get_private_crosswalk_dir(cls, workflow: str) -> Path:
        """Get the crosswalk directory for a proprietary matching workflow.

        Crosswalks derived from proprietary data are themselves proprietary and
        must not land in the committed ``crosswalk/`` tree; they live under
        ``PRIVATE_CROSSWALK_DIR`` instead.

        Args:
            workflow: Name of the workflow (e.g. 'vendor_source_hmda')

        Returns:
            Path under ``PRIVATE_CROSSWALK_DIR`` for the workflow's outputs
        """
        return cls.PRIVATE_CROSSWALK_DIR / workflow

    @classmethod
    def ensure_directories(cls, subpackage: str | None = None) -> None:
        """Create necessary directories if they don't exist.

        Args:
            subpackage: If provided, create medallion directories for this
                subpackage. If None, only create root directories.

        Example:
            >>> MortgageDataConfig.ensure_directories('hmda')
            >>> # Creates data/hmda/{raw,bronze,silver}
        """
        if subpackage:
            base = cls.get_subpackage_data_dir(subpackage)
            for stage in ["raw", "bronze", "silver"]:
                (base / stage).mkdir(parents=True, exist_ok=True)
        else:
            cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_project_root(cls) -> Path:
        """Get the project root directory.

        Returns:
            Path to the project root

        Example:
            >>> MortgageDataConfig.get_project_root()
            PosixPath('/Users/username/mortgage_data_manager')
        """
        return cls.PROJECT_DIR

    @classmethod
    def validate_paths(cls) -> bool:
        """Validate that all configured paths exist or can be created.

        Returns:
            True if all paths are valid, False otherwise

        Raises:
            PermissionError: If unable to create directories due to permissions
        """
        try:
            cls.ensure_directories()
            return True
        except PermissionError as e:
            raise PermissionError(
                f"Cannot create directories due to insufficient permissions: {e}"
            )
