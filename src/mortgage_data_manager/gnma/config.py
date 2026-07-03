"""GNMA Data Manager - Configuration.

Configuration for GNMA data processing pipeline.

This module provides:
    - GNMAConfig: Main configuration class inheriting from MortgageDataConfig
    - DownloadConfig: Configuration for data/schema downloading
    - ProcessorConfig: Configuration for data processing
    - SchemaReaderConfig: Configuration for schema reading

The GNMAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig

# ==========================================
# GNMAConfig - Main Configuration Class
# ==========================================


class GNMAConfig(MortgageDataConfig):
    """GNMA-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    GNMA-specific paths and constants.

    Environment Variables:
        GNMA_DATA_DIR: Root GNMA data directory
        GNMA_RAW_DIR: Raw data directory
        GNMA_BRONZE_DIR: Bronze layer directory (staged parquet)
        GNMA_SILVER_DIR: Silver layer directory (transformed data)
        GNMA_SCHEMAS_DIR: Schema files directory

    Example:
        >>> from mortgage_data_manager.gnma.config import GNMAConfig
        >>> print(GNMAConfig.GNMA_DATA_DIR)
        PosixPath('/path/to/data/gnma')
        >>> print(GNMAConfig.GNMA_BRONZE_DIR)
        PosixPath('/path/to/data/gnma/bronze')
    """

    # GNMA data directories
    GNMA_DATA_DIR: Path = Path(
        config("GNMA_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("gnma")))
    )
    GNMA_RAW_DIR: Path = Path(config("GNMA_RAW_DIR", default=str(GNMA_DATA_DIR / "raw")))
    GNMA_BRONZE_DIR: Path = Path(config("GNMA_BRONZE_DIR", default=str(GNMA_DATA_DIR / "bronze")))
    GNMA_SILVER_DIR: Path = Path(config("GNMA_SILVER_DIR", default=str(GNMA_DATA_DIR / "silver")))
    GNMA_GOLD_DIR: Path = Path(config("GNMA_GOLD_DIR", default=str(GNMA_DATA_DIR / "gold")))

    # Schema directories
    GNMA_SCHEMAS_DIR: Path = Path(
        config("GNMA_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "gnma"))
    )
    GNMA_PREFIX_DICTIONARY: Path = GNMA_SCHEMAS_DIR / "prefix_dictionary.yaml"
    GNMA_SCHEMA_CLEAN_DIR: Path = GNMA_SCHEMAS_DIR / "clean"
    GNMA_SCHEMA_COMBINED_DIR: Path = GNMA_SCHEMAS_DIR / "combined"

    # Backward compatibility aliases (maps old names to new)
    RAW_DIR = GNMA_RAW_DIR
    BRONZE_DIR = GNMA_BRONZE_DIR
    SILVER_DIR = GNMA_SILVER_DIR

    # Download constants
    BASE_URL: str = "https://bulk.ginniemae.gov/protectedfiledownload.aspx?dlfile=data_history_cons"
    BULK_URL: str = "https://bulk.ginniemae.gov"
    BULK_DOWNLOAD_URL: str = (
        "https://bulk.ginniemae.gov/protectedfiledownload.aspx?dlfile=data_bulk"
    )
    SCHEMA_BASE_URL: str = "https://www.ginniemae.gov/data_and_reports/disclosure_data/pages/disclosurehistoryfiles.aspx"
    # Master "Bulk Data Download Layout" page: a SharePoint LayoutsAndSamples list
    # carrying one ``<prefix>_layout.pdf`` per disclosure file. Covers prefixes the
    # per-prefix disclosurehistoryfiles scrape does not list (e.g. platcoll, HMBS
    # new-issue PS/S, issrcutoff, SRF, FRR).
    LAYOUT_BASE_URL: str = "https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx"
    SITE_BASE_URL: str = "https://www.ginniemae.gov"
    COOKIE_NAME: str = "GMProfileInfo"
    COOKIE_DOMAIN: str = "ginniemae.gov"
    COOKIE_PATH: str = "/"

    # Processing constants
    DEFAULT_DELIMITER: str = "|"
    TEXT_COLUMN_NAME: str = "text_content"
    DEFAULT_ENCODING: str = "utf-8"
    FALLBACK_ENCODING: str = "iso-8859-1"
    SUPPORTED_EXTENSIONS: tuple[str, ...] = (".txt", ".dat", ".csv")
    DEFAULT_CHUNK_SIZE: int = 100000
    DEFAULT_BATCH_SIZE: int = 100
    DEFAULT_DATE_FORMAT: str = "%Y%m"

    # Request/retry settings
    REQUEST_TIMEOUT_S: int = 30
    RETRY_TOTAL: int = 3
    RETRY_BACKOFF: float = 1.0
    RETRY_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)
    RETRY_ALLOWED_METHODS: tuple[str, ...] = ("GET",)

    # Streaming settings
    STREAM_CHUNK_SIZE: int = 1048576  # 1 MiB

    @classmethod
    def get_prefix_dir(
        cls,
        stage: Literal["raw", "bronze", "silver", "gold"],
        prefix: str,
    ) -> Path:
        """Get the prefix sub-directory under a GNMA medallion stage.

        GNMA organizes each medallion stage into per-prefix sub-directories
        (e.g. ``llmon1``, ``llmon2``, ``dailyllmni``). This helper resolves the
        prefix axis; for the bare stage directory use the per-dir attributes
        (``GNMA_SILVER_DIR``, etc.) or the inherited
        :meth:`MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> GNMAConfig.get_prefix_dir('silver', 'llmon1')
            PosixPath('/path/to/data/gnma/silver/llmon1')

        Note:
            ``get_medallion_dir`` is now inherited from
            :class:`MortgageDataConfig` unmodified (signature
            ``get_medallion_dir(subpackage, stage)``).
        """
        stage_dirs = {
            "raw": cls.GNMA_RAW_DIR,
            "bronze": cls.GNMA_BRONZE_DIR,
            "silver": cls.GNMA_SILVER_DIR,
            "gold": cls.GNMA_GOLD_DIR,
        }
        return stage_dirs[stage] / prefix

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary directories for GNMA data processing.

        Example:
            >>> GNMAConfig.ensure_directories()
            >>> # Creates: data/gnma/raw, data/gnma/bronze, etc.
        """
        # Create medallion directories
        for d in (cls.GNMA_RAW_DIR, cls.GNMA_BRONZE_DIR, cls.GNMA_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # Create schema directories
        cls.GNMA_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
        cls.GNMA_SCHEMA_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        cls.GNMA_SCHEMA_COMBINED_DIR.mkdir(parents=True, exist_ok=True)


# ==========================================
# Legacy Path Functions (Deprecated)
# ==========================================


def get_project_root() -> Path:
    """Get the project root directory.

    .. deprecated:: 2.1.0
        Use `GNMAConfig.PROJECT_DIR` instead.

    Returns:
        Path to project root directory
    """
    warnings.warn(
        "get_project_root() is deprecated. Use GNMAConfig.PROJECT_DIR instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return GNMAConfig.PROJECT_DIR


def get_data_root() -> Path:
    """Get the data root directory.

    .. deprecated:: 2.1.0
        Use `GNMAConfig.DATA_DIR` instead.

    Returns:
        Path to data directory
    """
    warnings.warn(
        "get_data_root() is deprecated. Use GNMAConfig.DATA_DIR instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return GNMAConfig.DATA_DIR


# ==========================================
# Backward Compatibility Exports
# ==========================================

# These module-level constants maintain backward compatibility
# with code that imports directly from config
PROJECT_ROOT = GNMAConfig.PROJECT_DIR
DATA_ROOT = GNMAConfig.DATA_DIR
GNMA_DATA = GNMAConfig.GNMA_DATA_DIR


@dataclass
class DownloadConfig:
    """Configuration for the GNMA downloader.

    This dataclass configures the download pipeline, including authentication,
    request settings, and output paths.

    Note:
        Path defaults now use GNMAConfig for consistency with the medallion
        architecture. The paths point to the raw layer for downloaded files.
    """

    # GNMA credentials and Page Settings
    email_value: str
    id_value: str
    user_agent: str = MortgageDataConfig.USER_AGENT
    base_url: str = GNMAConfig.BASE_URL
    cookie_name: str = GNMAConfig.COOKIE_NAME
    cookie_domain: str = GNMAConfig.COOKIE_DOMAIN
    cookie_path: str = GNMAConfig.COOKIE_PATH
    request_delay: float = 2.0
    cookie_expiry_days: int = 365
    schema_base_url: str = GNMAConfig.SCHEMA_BASE_URL
    layout_base_url: str = GNMAConfig.LAYOUT_BASE_URL
    site_base_url: str = GNMAConfig.SITE_BASE_URL

    # Folder settings - now using GNMAConfig paths
    data_download_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_RAW_DIR)
    schema_download_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SCHEMAS_DIR)
    prefix_file: Path = field(default_factory=lambda: GNMAConfig.GNMA_PREFIX_DICTIONARY)
    use_prefix_subfolders: bool = True
    create_prefix_folders: bool = True

    # Schema/PDF download settings
    bad_text_filters: list[str] | None = None

    # Request/Retry/Timeout settings
    request_timeout_s: int = GNMAConfig.REQUEST_TIMEOUT_S
    retry_total: int = GNMAConfig.RETRY_TOTAL
    retry_backoff: float = GNMAConfig.RETRY_BACKOFF
    retry_statuses: list[int] = field(default_factory=lambda: list(GNMAConfig.RETRY_STATUSES))
    retry_allowed_methods: list[str] = field(
        default_factory=lambda: list(GNMAConfig.RETRY_ALLOWED_METHODS)
    )

    # Download streaming settings
    stream_downloads: bool = False
    stream_chunk_size: int = GNMAConfig.STREAM_CHUNK_SIZE

    # Early exit for consecutive misses settings
    consecutive_miss_exit_threshold: int | None = None

    # Logging and behavior settings
    log_level: str = "INFO"
    logs_folder: Path = field(default_factory=lambda: GNMAConfig.PROJECT_DIR / "logs" / "gnma")
    require_link_on_page: bool = True


@dataclass
class SchemaReaderConfig:
    """Configuration for the GNMA schema reader.

    This dataclass configures schema extraction from PDFs and subsequent
    processing into combined schema files.

    Note:
        Path defaults now use GNMAConfig for consistency with the medallion
        architecture.
    """

    # Folder settings - now using GNMAConfig paths
    input_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_RAW_DIR)
    output_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_RAW_DIR)
    schemas_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SCHEMAS_DIR)
    prefix_file: Path = field(default_factory=lambda: GNMAConfig.GNMA_PREFIX_DICTIONARY)
    logs_folder: Path = field(default_factory=lambda: GNMAConfig.PROJECT_DIR / "logs" / "gnma")
    analysis_folder: Path = field(
        default_factory=lambda: GNMAConfig.PROJECT_DIR / "analysis" / "gnma"
    )

    # File processing settings
    text_column_name: str = GNMAConfig.TEXT_COLUMN_NAME
    encoding: str = GNMAConfig.DEFAULT_ENCODING
    fallback_encoding: str = GNMAConfig.FALLBACK_ENCODING
    supported_extensions: list[str] = field(
        default_factory=lambda: list(GNMAConfig.SUPPORTED_EXTENSIONS)
    )
    file_pattern: str = "*.zip"

    # Behavior settings
    skip_existing: bool = True
    log_level: str = "INFO"
    batch_size: int = GNMAConfig.DEFAULT_BATCH_SIZE
    validate_conversions: bool = True
    use_prefix_subfolders: bool = True
    verbose: bool = False
    overwrite: bool = False

    # Heuristic flags
    merge_continuations: bool = True
    apply_item_grouping: bool = True
    extract_record_types: bool = True

    # Analysis settings
    save_analysis: bool = False


@dataclass
class ProcessorConfig:
    """Configuration for the GNMA data processor.

    This dataclass configures the data processing pipeline, including staging
    (raw → bronze) and transformation (bronze → silver).

    Note:
        Path defaults now use GNMAConfig with medallion architecture terminology:
        - raw_folder: Source raw files (ZIP, TXT)
        - bronze_folder: Staged parquet files (replaces old 'raw' parquet output)
        - silver_folder: Transformed, structured data (replaces old 'clean')
    """

    # Folder settings - using GNMAConfig with medallion architecture
    # raw_folder: Source of raw downloaded files
    raw_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_RAW_DIR)
    # bronze_folder: Output for staged parquet files (was raw/*.parquet)
    bronze_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_BRONZE_DIR)
    # silver_folder: Output for transformed data (was clean/)
    silver_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SILVER_DIR)
    # Schema folders
    schema_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SCHEMA_COMBINED_DIR)
    schemas_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SCHEMAS_DIR)
    prefix_file: Path = field(default_factory=lambda: GNMAConfig.GNMA_PREFIX_DICTIONARY)
    logs_folder: Path = field(default_factory=lambda: GNMAConfig.PROJECT_DIR / "logs" / "gnma")

    # Backward compatibility aliases (deprecated, use bronze_folder/silver_folder)
    @property
    def clean_folder(self) -> Path:
        """Alias for silver_folder (deprecated)."""
        warnings.warn(
            "clean_folder is deprecated. Use silver_folder instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.silver_folder

    # General behavior
    skip_existing: bool = True
    log_level: str = "INFO"
    batch_size: int = 50
    validate_outputs: bool = True
    create_directories: bool = True
    verbose: bool = False

    # Parsing/format detection
    default_delimiter: str = GNMAConfig.DEFAULT_DELIMITER
    date_format: str = GNMAConfig.DEFAULT_DATE_FORMAT
    fallback_to_manual_processing: bool = True

    # Staging settings
    text_column_name: str = GNMAConfig.TEXT_COLUMN_NAME
    encoding: str = GNMAConfig.DEFAULT_ENCODING
    fallback_encoding: str = GNMAConfig.FALLBACK_ENCODING
    supported_extensions: list[str] = field(
        default_factory=lambda: list(GNMAConfig.SUPPORTED_EXTENSIONS)
    )
    chunk_size: int = GNMAConfig.DEFAULT_CHUNK_SIZE
