#!/usr/bin/env python3
"""GNMA Data Manager - Modular Package.

A modular package for downloading, processing, and analyzing GNMA
(Government National Mortgage Association) data files.

This package follows the project's medallion architecture:
    - raw: Downloaded ZIP/TXT files from GNMA bulk data portal
    - bronze: Staged parquet files with text_content column
    - silver: Transformed, structured data with schema-applied columns

Configuration:
    The GNMAConfig class inherits from MortgageDataConfig and provides
    GNMA-specific paths and constants. All paths can be overridden via
    environment variables (GNMA_DATA_DIR, GNMA_RAW_DIR, etc.).

Modules:
    - download: Data and schema file downloading to raw layer
    - schemas: PDF schema extraction, COBOL parsing, cleaning, and analysis
    - import_bronze: Data staging (raw → bronze)
    - import_silver: Data transformation (bronze → silver)
    - performance: Performance data loading and analysis from silver layer
    - config: GNMAConfig class and configuration dataclasses
    - utils: Shared utilities, result classes, and exceptions

Example:
    >>> from mortgage_data_manager.gnma import GNMAConfig
    >>> print(GNMAConfig.GNMA_RAW_DIR)     # Downloaded files
    >>> print(GNMAConfig.GNMA_BRONZE_DIR)  # Staged parquet
    >>> print(GNMAConfig.GNMA_SILVER_DIR)  # Transformed data

"""

# Import main configuration classes
from .config import DownloadConfig, GNMAConfig, ProcessorConfig, SchemaReaderConfig

# Import key functions from submodules
from .download import (
    create_session,
    download_all_data,
    download_all_schemas,
    download_data_file,
    download_prefix_data,
    download_prefix_schemas,
    download_schema_file,
    find_data_links,
    find_schema_links,
)

# Import bronze layer functions (raw → bronze)
from .import_bronze import (
    create_prefix_subfolders,
    import_all_bronze,
    import_prefix_bronze,
    import_txt_to_parquet,
    import_zip_to_parquet,
)

# Import silver layer functions (bronze → silver)
from .import_silver import (
    analyze_prefix_format,
    detect_file_format,
    import_file_silver,
    import_prefix_silver,
    load_schema_by_record_type,
    transform_delimited,
    transform_fixed_width,
)
from .performance import create_upb_time_series, get_summary_statistics, load_performance_data
from .pipeline import (
    print_pipeline_summary,
    run_analysis,
    run_download,
    run_pipeline,
    run_processing,
    run_schemas,
)
from .schemas import (
    add_record_type_column,
    analyze_schema_formats,
    # Analysis
    analyze_temporal_coverage,
    augment_with_cobol_dtypes,
    # Cleaning
    clean_extracted_dataframe,
    combine_all_schemas,
    # Combination
    combine_schemas,
    convert_cobol_value,
    count_field_names_from_schemas,
    create_pandas_dtype_mapping,
    create_polars_dtype_mapping,
    create_polars_schema,
    extract_dates_from_filename,
    extract_record_type_code,
    extract_schemas_to_csv,
    # PDF extraction
    extract_tables_from_pdf,
    merge_continuation_table,
    # COBOL
    parse_cobol_format,
    propagate_record_types_to_groups,
    read_combined_schemas,
    reconcile_schema_versions,
    reconcile_schemas,
    standardize_columns,
    standardize_field_names,
    # Standardization
    standardize_single_name,
)

# Import utils: result classes, exceptions, and utility functions
from .utils import (
    ConversionResult,
    DateFormatError,
    DownloadError,
    ProcessingResult,
    SchemaReaderError,
    create_default_configs,
    load_prefix_dictionary,
)

__all__ = [
    # Configuration
    'GNMAConfig',
    'DownloadConfig',
    'ProcessorConfig',
    'SchemaReaderConfig',

    # Result classes
    'ConversionResult',
    'ProcessingResult',

    # Exceptions
    'DateFormatError',
    'DownloadError',
    'SchemaReaderError',

    # Download functions
    'create_session',
    'download_data_file',
    'download_prefix_data',
    'download_all_data',
    'download_schema_file',
    'download_prefix_schemas',
    'download_all_schemas',
    'find_data_links',
    'find_schema_links',

    # Performance functions
    'load_performance_data',
    'create_upb_time_series',
    'get_summary_statistics',

    # Pipeline functions
    'run_download',
    'run_analysis',
    'run_processing',
    'run_pipeline',
    'run_schemas',
    'print_pipeline_summary',

    # Schema functions - PDF extraction
    'extract_tables_from_pdf',
    'extract_schemas_to_csv',
    'standardize_columns',
    'merge_continuation_table',
    # Schema functions - Cleaning
    'clean_extracted_dataframe',
    'extract_record_type_code',
    'add_record_type_column',
    'propagate_record_types_to_groups',
    # Schema functions - Standardization
    'standardize_single_name',
    'standardize_field_names',
    'count_field_names_from_schemas',
    # Schema functions - Analysis
    'extract_dates_from_filename',
    'analyze_temporal_coverage',
    'analyze_schema_formats',
    # Schema functions - Combination
    'combine_schemas',
    'combine_all_schemas',
    'read_combined_schemas',
    'reconcile_schema_versions',
    'reconcile_schemas',
    # Schema functions - COBOL
    'parse_cobol_format',
    'convert_cobol_value',
    'augment_with_cobol_dtypes',
    'create_pandas_dtype_mapping',
    'create_polars_dtype_mapping',
    'create_polars_schema',

    # Utils functions
    'load_prefix_dictionary',
    'create_default_configs',

    # Bronze layer functions (raw → bronze)
    'import_zip_to_parquet',
    'import_txt_to_parquet',
    'import_prefix_bronze',
    'import_all_bronze',
    'create_prefix_subfolders',
    # Silver layer functions (bronze → silver)
    'detect_file_format',
    'load_schema_by_record_type',
    'analyze_prefix_format',
    'transform_fixed_width',
    'transform_delimited',
    'import_file_silver',
    'import_prefix_silver',
]

