"""Download utilities for UMBS (Uniform Mortgage-Backed Securities) data.

MANUAL DOWNLOAD REQUIRED - Automated download not supported due to Terms of Service.

UMBS data is available through both Fannie Mae (Data Dynamics) and Freddie Mac
(Clarity Data Intelligence) platforms. Users must register on one or both
platforms and manually download files.

Example:
    >>> from mortgage_data_manager.umbs.download import get_download_instructions
    >>> print(get_download_instructions())
"""

from __future__ import annotations

# Flag indicating manual download is required
MANUAL_DOWNLOAD_REQUIRED: bool = True

# Fannie Mae portal URL
FANNIE_MAE_URL: str = "https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs"

# Freddie Mac portal URL
FREDDIE_MAC_URL: str = "https://capitalmarkets.freddiemac.com/mbs/products/umbs"

# Data portal names
FANNIE_MAE_PORTAL: str = "Data Dynamics"
FREDDIE_MAC_PORTAL: str = "Clarity Data Intelligence"


def get_download_instructions() -> str:
    """Return formatted download instructions for UMBS data.

    Returns:
        Multi-line string with step-by-step download instructions

    Example:
        >>> instructions = get_download_instructions()
        >>> print(instructions)
    """
    return f"""
================================================================================
UMBS DATA - MANUAL DOWNLOAD REQUIRED
================================================================================

Automated download is not supported due to Terms of Service restrictions.

UMBS data is available through both Fannie Mae and Freddie Mac platforms.

ACCESS OPTION 1: FANNIE MAE
  Portal: {FANNIE_MAE_PORTAL}
  URL: {FANNIE_MAE_URL}
  Registration: Data Dynamics account (free)
  Data Available: MBS issuance, monthly disclosures, supplemental info

ACCESS OPTION 2: FREDDIE MAC
  Portal: {FREDDIE_MAC_PORTAL}
  URL: {FREDDIE_MAC_URL}
  Registration: Clarity Data Intelligence account (free)
  Data Available: CRT issuance, monthly disclosures, MBS info

STEPS TO DOWNLOAD:

1. Choose a portal (Fannie Mae or Freddie Mac)

2. Register for a free account

3. Navigate to UMBS/MBS data section

4. Download desired files

5. Place downloaded files in: data/umbs/raw/

AFTER DOWNLOAD:

Process raw files to bronze layer:
  $ mortgage-data umbs import bronze

ABOUT UMBS:

  - Issued since June 2019 (Single Security Initiative)
  - Backed by 30-, 20-, 15-, and 10-year fixed-rate mortgages
  - Same characteristics regardless of issuer (Fannie or Freddie)
  - TBA-eligible securities

================================================================================
"""


def download(*args, **kwargs) -> None:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but UMBS data requires manual download.

    Raises:
        NotImplementedError: Always, with instructions for manual download

    Example:
        >>> from mortgage_data_manager.umbs.download import download
        >>> download()  # Raises NotImplementedError
    """
    raise NotImplementedError(get_download_instructions())


def discover_files(*args, **kwargs) -> list:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but UMBS data requires manual download.

    Raises:
        NotImplementedError: Always, with instructions for manual download
    """
    raise NotImplementedError(get_download_instructions())
