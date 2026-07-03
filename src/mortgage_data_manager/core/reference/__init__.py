"""Reference data utilities (stable external tables).

Reference data is data that:
  - originates outside any subpackage (e.g., Census Bureau),
  - is stable / rarely reissued, and
  - is consumed by multiple subpackages or matching workflows.

Files are cached as parquet under `MortgageDataConfig.DATA_DIR / "reference"`.

Most reference tables here are stable (rarely reissued). The macro series in
:mod:`core.reference.macro` are the exception — they are refreshed regularly by
their publishers and expose an ``overwrite`` flag to re-pull.
"""

from mortgage_data_manager.core.reference.census_tract import (
    VINTAGE_2020_FIRST_YEAR,
    load_tract_relationship,
    needs_vintage_bridge,
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

__all__ = [
    "load_tract_relationship",
    "needs_vintage_bridge",
    "VINTAGE_2020_FIRST_YEAR",
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
]
