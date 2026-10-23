"""Reference data utilities (stable external tables).

Reference data is data that:
  - originates outside any subpackage (e.g., Census Bureau),
  - is stable / rarely reissued, and
  - is consumed by multiple subpackages or matching workflows.

Files are cached under `MortgageDataConfig.REFERENCE_DIR` (default `DATA_DIR / "reference"`),
one subfolder per source.

Most reference tables here are stable (rarely reissued). The macro series in
`core.reference.macro` are the exception — they are refreshed regularly by
their publishers and expose an ``overwrite`` flag to re-pull.
"""

from mortgage_data_manager.core.reference.avery import (
    AVERY_FILES,
    avery_dir,
    avery_file_path,
    download_avery_files,
)
from mortgage_data_manager.core.reference.census_tract import (
    VINTAGE_2010_FIRST_YEAR,
    VINTAGE_2020_FIRST_YEAR,
    load_tract_relationship,
    load_tract_relationship_2000_2010,
    needs_vintage_bridge,
    needs_vintage_bridge_2000_2010,
)
from mortgage_data_manager.core.reference.ct_planning_region import (
    PLANREG_FIRST_YEAR,
    load_ct_planreg_relationship,
    needs_ct_planreg_bridge,
    to_county_vintage,
    to_planning_region,
)
from mortgage_data_manager.core.reference.gnma_pool_types import (
    GNMA_ISSUE_TYPES,
    GNMA_POOL_TYPES,
    GNMA_PROGRAM_BY_ISSUE_TYPE,
    issue_type_to_program,
    pool_type_family,
    pool_type_is_arm,
)
from mortgage_data_manager.core.reference.macro import (
    MACRO_START,
    load_hpi,
    load_macro_series,
    load_pmms,
    load_unemployment,
    load_yield_curve,
    refresh_macro_series,
)
from mortgage_data_manager.core.reference.states import (
    FIPS_INT_TO_STATE,
    FIPS_TO_STATE,
    STATE_TO_FIPS,
    TERRITORIES,
)
from mortgage_data_manager.core.reference.tract_zip3 import (
    TRACT_ZIP3_VINTAGES,
    census_year_to_vintage_expr,
    load_dominant_tract_zip3,
    load_tract_zip3,
)

__all__ = [
    "AVERY_FILES",
    "avery_dir",
    "avery_file_path",
    "download_avery_files",
    "load_tract_relationship",
    "needs_vintage_bridge",
    "VINTAGE_2020_FIRST_YEAR",
    "load_tract_relationship_2000_2010",
    "needs_vintage_bridge_2000_2010",
    "VINTAGE_2010_FIRST_YEAR",
    "load_ct_planreg_relationship",
    "needs_ct_planreg_bridge",
    "to_planning_region",
    "to_county_vintage",
    "PLANREG_FIRST_YEAR",
    "GNMA_POOL_TYPES",
    "GNMA_ISSUE_TYPES",
    "GNMA_PROGRAM_BY_ISSUE_TYPE",
    "pool_type_family",
    "pool_type_is_arm",
    "issue_type_to_program",
    "load_macro_series",
    "load_pmms",
    "load_hpi",
    "load_unemployment",
    "load_yield_curve",
    "refresh_macro_series",
    "MACRO_START",
    "STATE_TO_FIPS",
    "FIPS_TO_STATE",
    "FIPS_INT_TO_STATE",
    "TERRITORIES",
    "load_tract_zip3",
    "load_dominant_tract_zip3",
    "census_year_to_vintage_expr",
    "TRACT_ZIP3_VINTAGES",
]
