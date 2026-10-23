"""Download utilities for Fannie Mae (FNMA) data.

MANUAL DOWNLOAD REQUIRED - Automated download not supported due to Terms of Service.

Fannie Mae Single-Family Loan Performance Data requires free registration
on the Data Dynamics platform. Users must manually download files from
the portal and place them in the raw data directory.

Example:
    >>> from mortgage_data_manager.sources.fnma.download import get_manual_download_instructions
    >>> print(get_manual_download_instructions())
"""

from __future__ import annotations

# Registration URL
REGISTRATION_URL: str = "https://capitalmarkets.fanniemae.com/credit-risk-transfer/fannie-mae-single-family-loan-performance-data"

# Data portal name
DATA_PORTAL_NAME: str = "Data Dynamics"


def get_manual_download_instructions() -> str:
    """Return formatted download instructions for Fannie Mae data.

    Returns:
        Multi-line string with step-by-step download instructions

    Example:
        >>> instructions = get_manual_download_instructions()
        >>> print(instructions)
    """
    return f"""
================================================================================
FANNIE MAE DATA - MANUAL DOWNLOAD REQUIRED
================================================================================

Automated download is not supported due to Terms of Service restrictions.

DATA PORTAL: {DATA_PORTAL_NAME}
URL: {REGISTRATION_URL}

STEPS TO DOWNLOAD:

1. Visit the Fannie Mae data portal:
   {REGISTRATION_URL}

2. Click "Access the Data" to reach Data Dynamics

3. Register for a free account:
   - Email address
   - Password
   - Accept Terms and Conditions

4. Navigate to Single-Family Loan Performance Data

5. Download files:
   - Full Dataset: Use "Entire Single Family Dataset" for bulk download
   - By Quarter: Download individual quarterly files

6. Place downloaded files in: data/fnma/raw/

AFTER DOWNLOAD:

Process raw files to bronze layer:
  $ mortgage-data fnma bronze

DATASET INFORMATION:

  - Coverage: 30-year and shorter fixed-rate single-family mortgages
  - Fields: 110 data fields (2024 format)
  - Updates: Quarterly (after the 20th following quarter-end)
  - Cost: Free for research/academic use

================================================================================
"""


def download(*args: object, **kwargs: object) -> None:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but Fannie Mae data requires manual download.

    Args:
        *args: Ignored; accepted for interface parity with other subpackages.
        **kwargs: Ignored; accepted for interface parity with other subpackages.

    Raises:
        NotImplementedError: Always, with instructions for manual download

    Example:
        >>> from mortgage_data_manager.sources.fnma.download import download
        >>> download()  # Raises NotImplementedError
    """
    raise NotImplementedError(get_manual_download_instructions())
