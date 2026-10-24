"""Configuration for Ginnie Mae (GNMA) data.

Configuration for GNMA data processing pipeline.

This module provides:
    - GNMAConfig: Main configuration class inheriting from MortgageDataConfig
    - DownloadConfig: Configuration for data/schema downloading
    - ProcessorConfig: Configuration for data processing

The GNMAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

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
        >>> from mortgage_data_manager.sources.gnma.config import GNMAConfig
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
    GNMA_CANONICAL_DIR: Path = GNMA_SCHEMAS_DIR / "canonical"

    # Download constants (July 2026 site rebuild: the SharePoint/aspx pages were
    # replaced by Angular SPAs backed by JSON APIs; the GMProfileInfo cookie auth
    # was replaced by a bearer token from the user-profiles service).
    DISCLOSURE_API_URL: str = "https://www.ginniemae.gov/disclosure-api/api"
    USER_PROFILE_API_URL: str = "https://www.ginniemae.gov/user-profiles/api"
    BULK_CONTENT_API_URL: str = "https://www.ginniemae.gov/bulk-content/api"
    DOWNLOAD_URL: str = f"{DISCLOSURE_API_URL}/download"
    HISTORY_DIRECTORY: str = "data_history_cons"
    BULK_DIRECTORY: str = "data_bulk"
    BASE_URL: str = f"{DOWNLOAD_URL}?dlfile={HISTORY_DIRECTORY}"
    BULK_DOWNLOAD_URL: str = f"{DOWNLOAD_URL}?dlfile={BULK_DIRECTORY}"
    SITE_BASE_URL: str = "https://www.ginniemae.gov"
    COOKIE_NAME: str = "gm_up_token"
    COOKIE_DOMAIN: str = "ginniemae.gov"
    COOKIE_PATH: str = "/"
    TOKEN_CACHE_FILE: Path = GNMA_DATA_DIR / ".gm_up_token"

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
        `MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> GNMAConfig.get_prefix_dir('silver', 'llmon1')
            PosixPath('/path/to/data/gnma/silver/llmon1')

        Note:
            ``get_medallion_dir`` is inherited from `MortgageDataConfig`
            unmodified (signature ``get_medallion_dir(subpackage, stage)``).
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

        # Create schema directory (canonical JSON schemas live under it)
        cls.GNMA_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DownloadConfig:
    """Configuration for the GNMA downloader.

    This dataclass configures the download pipeline, including authentication,
    request settings, and output paths.

    Note:
        Path defaults come from GNMAConfig and point to the raw layer for
        downloaded files.
    """

    # GNMA credentials and API settings. ``answer_value`` is the answer to the
    # profile's secret question (shown by the auth lookup endpoint).
    email_value: str
    answer_value: str = ""
    user_agent: str = MortgageDataConfig.USER_AGENT
    base_url: str = GNMAConfig.BASE_URL
    disclosure_api_url: str = GNMAConfig.DISCLOSURE_API_URL
    user_profile_api_url: str = GNMAConfig.USER_PROFILE_API_URL
    bulk_content_api_url: str = GNMAConfig.BULK_CONTENT_API_URL
    cookie_name: str = GNMAConfig.COOKIE_NAME
    cookie_domain: str = GNMAConfig.COOKIE_DOMAIN
    cookie_path: str = GNMAConfig.COOKIE_PATH
    request_delay: float = 2.0
    token_cache_file: Path = field(default_factory=lambda: GNMAConfig.TOKEN_CACHE_FILE)
    site_base_url: str = GNMAConfig.SITE_BASE_URL

    # Folder settings from GNMAConfig paths
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
class ProcessorConfig:
    """Configuration for the GNMA data processor.

    This dataclass configures the data processing pipeline, including staging
    (raw → bronze) and transformation (bronze → silver).

    Note:
        Path defaults come from GNMAConfig's medallion directories:
        - raw_folder: Source raw files (ZIP, TXT)
        - bronze_folder: Staged parquet files
        - silver_folder: Transformed, structured data
    """

    # Folder settings - using GNMAConfig with medallion architecture
    # raw_folder: Source of raw downloaded files
    raw_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_RAW_DIR)
    # bronze_folder: Output for staged parquet files (was raw/*.parquet)
    bronze_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_BRONZE_DIR)
    # silver_folder: Output for transformed data (was clean/)
    silver_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SILVER_DIR)
    # Schema folder (canonical JSON lives under schemas_folder/"canonical")
    schemas_folder: Path = field(default_factory=lambda: GNMAConfig.GNMA_SCHEMAS_DIR)
    prefix_file: Path = field(default_factory=lambda: GNMAConfig.GNMA_PREFIX_DICTIONARY)
    logs_folder: Path = field(default_factory=lambda: GNMAConfig.PROJECT_DIR / "logs" / "gnma")

    # General behavior
    overwrite: bool = False
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


# ----------------------------------------------------------------------------- #
# Download dataset registry
# ----------------------------------------------------------------------------- #

#: What ``gnma download`` can fetch, and from where.
DOWNLOAD_DATASETS: Final[dict[str, str]] = {
    "data": "Data files from the per-prefix disclosure history page.",
    "schemas": "Schema definition PDFs (see --source for which page).",
    "bulk": "Bulk page (bulk.ginniemae.gov) — this month's files, published early.",
}

#: The default set. ``bulk`` is deliberately excluded: it fetches provisional
#: copies that a later history-page download replaces, so it stays opt-in.
DEFAULT_DOWNLOAD_DATASETS: Final[list[str]] = ["data", "schemas"]


def resolve_download_datasets(datasets: list[str] | None) -> list[str]:
    """Default to `DEFAULT_DOWNLOAD_DATASETS`; validate any explicit subset.

    Args:
        datasets: Dataset identifiers, or ``None`` for the default set.

    Returns:
        Validated list of dataset identifiers.

    Raises:
        ValueError: If an unknown dataset identifier is given.
    """
    if datasets is None:
        return list(DEFAULT_DOWNLOAD_DATASETS)
    unknown = [d for d in datasets if d not in DOWNLOAD_DATASETS]
    if unknown:
        raise ValueError(
            f"Unknown GNMA download datasets: {unknown}; valid: {list(DOWNLOAD_DATASETS)}"
        )
    return list(datasets)
