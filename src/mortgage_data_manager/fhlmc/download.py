"""Download utilities for Freddie Mac (FHLMC) data.

MANUAL DOWNLOAD REQUIRED - Automated download not supported due to Terms of Service.

Freddie Mac Single-Family Loan-Level Dataset requires free registration
on the Clarity Data Intelligence platform. Users must manually download
files from the portal and place them in the raw data directory.

Example:
    >>> from mortgage_data_manager.fhlmc.download import get_download_instructions
    >>> print(get_download_instructions())
"""

from __future__ import annotations

# Flag indicating manual download is required
MANUAL_DOWNLOAD_REQUIRED: bool = True

# Registration URL
REGISTRATION_URL: str = "https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset"

# Clarity portal URL
CLARITY_URL: str = "https://capitalmarkets.freddiemac.com/clarity"

# Data portal name
DATA_PORTAL_NAME: str = "Clarity Data Intelligence"


def get_download_instructions() -> str:
    """Return formatted download instructions for Freddie Mac data.

    Returns:
        Multi-line string with step-by-step download instructions

    Example:
        >>> instructions = get_download_instructions()
        >>> print(instructions)
    """
    return f"""
================================================================================
FREDDIE MAC DATA - MANUAL DOWNLOAD REQUIRED
================================================================================

Automated download is not supported due to Terms of Service restrictions.

DATA PORTAL: {DATA_PORTAL_NAME}
INFO URL: {REGISTRATION_URL}
PORTAL URL: {CLARITY_URL}

STEPS TO DOWNLOAD:

1. Visit the Freddie Mac data portal:
   {REGISTRATION_URL}

2. Click to access Clarity Data Intelligence:
   {CLARITY_URL}

3. Register for a free account

4. Navigate to SFLLD Data Download and choose:
   - Full Dataset: All historical data
   - Standard Dataset by Year: Annual files
   - Non-Standard Dataset: ARMs, balloons, etc.
   - Sample Files: For testing

5. Place downloaded files in: data/fhlmc/raw/

AFTER DOWNLOAD:

Process raw files to bronze layer:
  $ mortgage-data fhlmc import bronze

DATASET INFORMATION:

  - Coverage: ~54.8 million mortgages (1999-2024)
  - Datasets: Standard, Non-Standard, RPL Mapping
  - Updates: Quarterly
  - Cost: Free for non-commercial/research use

SUPPORT:

  Technical issues: SF_LLD_Technology_Support@freddiemac.com

================================================================================
"""


def download(*args, **kwargs) -> None:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but Freddie Mac data requires manual download.

    Raises:
        NotImplementedError: Always, with instructions for manual download

    Example:
        >>> from mortgage_data_manager.fhlmc.download import download
        >>> download()  # Raises NotImplementedError
    """
    raise NotImplementedError(get_download_instructions())


def discover_files(*args, **kwargs) -> list:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but Freddie Mac data requires manual download.

    Raises:
        NotImplementedError: Always, with instructions for manual download
    """
    raise NotImplementedError(get_download_instructions())
