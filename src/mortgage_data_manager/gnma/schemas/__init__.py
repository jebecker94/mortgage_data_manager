#!/usr/bin/env python3
"""GNMA Data Manager - Schema Module.

Functions for extracting, cleaning, and analyzing GNMA schema definitions.

Submodules:
    - pdf_extraction: Extract tables from PDF files
    - cleaning: Clean and standardize extracted data
    - standardization: Standardize field names and values
    - cobol: Parse COBOL format notation
    - combination: Combine multiple schema versions
    - analysis: Analyze schema coverage and formats

"""

# Import key functions for easy access
from .analysis import analyze_schema_formats, analyze_temporal_coverage
from .cleaning import (
    add_record_type_column,
    clean_extracted_dataframe,
    extract_dates_from_filename,
    extract_record_type_code,
    propagate_record_types_to_groups,
)
from .cobol import (
    augment_with_cobol_dtypes,
    convert_cobol_value,
    create_pandas_dtype_mapping,
    create_polars_dtype_mapping,
    create_polars_schema,
    parse_cobol_format,
)
from .combination import (
    combine_all_schemas,
    combine_schemas,
    read_combined_schemas,
    reconcile_schema_versions,
    reconcile_schemas,
)
from .pdf_extraction import (
    extract_schemas_to_csv,
    extract_tables_from_pdf,
    merge_continuation_table,
    standardize_columns,
)
from .standardization import (
    count_field_names_from_schemas,
    standardize_field_names,
    standardize_single_name,
)

__all__ = [
    # PDF extraction
    'extract_tables_from_pdf',
    'extract_schemas_to_csv',
    'standardize_columns',
    'merge_continuation_table',
    # Cleaning functions
    'clean_extracted_dataframe',
    'extract_record_type_code',
    'add_record_type_column',
    'propagate_record_types_to_groups',
    'extract_dates_from_filename',
    # Standardization
    'standardize_single_name',
    'standardize_field_names',
    'count_field_names_from_schemas',
    # Analysis
    'analyze_temporal_coverage',
    'analyze_schema_formats',
    # Combination
    'combine_schemas',
    'combine_all_schemas',
    'read_combined_schemas',
    'reconcile_schema_versions',
    'reconcile_schemas',
    # COBOL
    'parse_cobol_format',
    'convert_cobol_value',
    'augment_with_cobol_dtypes',
    'create_pandas_dtype_mapping',
    'create_polars_dtype_mapping',
    'create_polars_schema',
]

