"""Manual-download instructions and raw coverage for UMBS disclosure files.

Automated download is not supported: both GSEs publish the disclosure files behind a
registered login (Fannie's PoolTalk, Freddie's disclosure site) or on SFTP. The files
are placed by hand under ``raw/{GSE}/{folder}/``. `RAW_FEEDS` lists the feeds the
silver layer (and the combined builders) read, `get_manual_download_instructions`
renders them as instructions, and `raw_coverage` reports what is on disk.

Example:
    >>> from mortgage_data_manager.sources.umbs.download import get_manual_download_instructions
    >>> print(get_manual_download_instructions())
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import polars as pl

# Fannie Mae portal URL
FANNIE_MAE_URL: str = (
    "https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs"
)

# Freddie Mac portal URL
FREDDIE_MAC_URL: str = "https://capitalmarkets.freddiemac.com/mbs/products/umbs"

# Data portal names
FANNIE_MAE_PORTAL: str = "PoolTalk (Files & Reports) or Fannie Mae SFTP"
FREDDIE_MAC_PORTAL: str = "Freddie Mac disclosure files site or SFTP"


@dataclass(frozen=True)
class RawFeed:
    """One UMBS disclosure feed that A reads, and where its raw files go.

    Attributes:
        gse: ``FNMA`` or ``FHLMC`` (the first level under ``raw/``).
        folder: The folder under ``raw/{gse}/``, which is also the bronze folder.
        prefix: The publisher's name for the feed.
        file_pattern: The published file name, ``YYYYMM`` or ``YYMMDD`` for the date.
        first_month: First month published (``YYYY-MM``).
        description: What the feed is.
    """

    gse: str
    folder: str
    prefix: str
    file_pattern: str
    first_month: str
    description: str


#: Every feed A reads. RILLD is the only late starter: the GSEs introduced it in 2026, and
#: the first file is issuance month 2026-03 (see ``config.CORRECTION_PAIRS``).
RAW_FEEDS: tuple[RawFeed, ...] = (
    RawFeed("FNMA", "FNM_ILLD", "ILLD", "FNM_ILLD_YYYYMM.zip", "2019-06", "issuance loan level"),
    RawFeed(
        "FNMA", "FNM_RILLD", "RILLD", "FNM_RILLD_YYYYMM.zip", "2026-03", "loan-level corrections"
    ),
    RawFeed("FNMA", "FNM_IS", "IS", "FNM_IS_YYYYMM.zip", "2019-06", "issuance security level"),
    RawFeed("FNMA", "FNM_RIS", "RIS", "FNM_RIS_YYYYMM.zip", "2019-06", "security corrections"),
    RawFeed("FNMA", "FNM_MLLD", "MLLD", "FNM_MLLD_YYYYMM.zip", "2019-06", "monthly loan level"),
    RawFeed("FNMA", "FNM_MF", "MF", "FNM_MF_YYYYMM.zip", "2019-06", "monthly security factor"),
    RawFeed(
        "FNMA",
        "FNM_GN_MEGA",
        "GN_MEGA",
        "FNM_GN_MEGA_YYYYMM.zip",
        "2019-06",
        "Megas backed by Ginnie Mae collateral",
    ),
    RawFeed("FHLMC", "FRE_ILLD", "ILLD", "FRE_ILLD_YYYYMM.zip", "2019-06", "issuance loan level"),
    RawFeed(
        "FHLMC", "FRE_RILLD", "RILLD", "FRE_RILLD_YYYYMM.zip", "2026-03", "loan-level corrections"
    ),
    RawFeed("FHLMC", "FRE_IS", "IS", "FRE_IS_YYYYMM.zip", "2019-06", "issuance security level"),
    RawFeed("FHLMC", "FRE_RIS", "RIS", "FRE_RIS_YYYYMM.zip", "2019-06", "security corrections"),
    RawFeed("FHLMC", "FU", "FU", "fuYYMMDD.zip", "2019-06", "monthly loan level"),
    RawFeed("FHLMC", "FD", "FD", "fdYYMMDD.zip", "2019-06", "monthly security factor"),
)

_UNDERSCORE_MONTH = re.compile(r"_(\d{4})(\d{2})(?:_[A-Za-z0-9]+)?\.(?:zip|txt)$", re.IGNORECASE)
_TWO_LETTER_MONTH = re.compile(r"^[a-z]{2}(\d{2})(\d{2})\d{2}\.zip$", re.IGNORECASE)


def raw_file_month(name: str) -> str | None:
    """The disclosure month (``YYYY-MM``) of a raw UMBS file name, or None.

    Reads both conventions: ``FNM_ILLD_202507.zip`` (with an optional republication
    suffix such as ``_REV`` or ``_2``) and Freddie's ``fu261006.zip`` (``YYMMDD``).

    Args:
        name: The file name.

    Returns:
        The month, or None when the name follows neither convention.

    Example:
        >>> raw_file_month("FNM_IS_202509_REV.zip"), raw_file_month("fd210806.zip")
        ('2025-09', '2021-08')
    """
    if m := _UNDERSCORE_MONTH.search(name):
        return f"{m.group(1)}-{m.group(2)}"
    if m := _TWO_LETTER_MONTH.match(name):
        return f"20{m.group(1)}-{m.group(2)}"
    return None


def _month_range(first: str, last: str) -> list[str]:
    """Every ``YYYY-MM`` from ``first`` to ``last`` inclusive."""
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def raw_coverage(raw_dir: Path) -> pl.DataFrame:
    """Per-feed coverage of the raw UMBS files on disk.

    Missing months are counted from the feed's first published month (not from the
    first file on disk) through the last file on disk, so a gap at the start shows.

    Args:
        raw_dir: The UMBS raw root (``UMBSConfig.UMBS_RAW_DIR``).

    Returns:
        One row per `RAW_FEEDS` entry: ``gse``, ``prefix``, ``folder``, ``files``,
        ``first_month``, ``last_month``, ``missing_months`` (list) and
        ``multi_file_months`` (list of months holding more than one file, e.g. an
        original plus a ``_REV`` republication).
    """
    rows = []
    for feed in RAW_FEEDS:
        folder = raw_dir / feed.gse / feed.folder
        files = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []
        months = [mo for mo in (raw_file_month(n) for n in files) if mo is not None]
        counts: dict[str, int] = {}
        for mo in months:
            counts[mo] = counts.get(mo, 0) + 1
        first = min(counts) if counts else None
        last = max(counts) if counts else None
        expected = _month_range(feed.first_month, last) if last else []
        rows.append(
            {
                "gse": feed.gse,
                "prefix": feed.prefix,
                "folder": feed.folder,
                "files": len(files),
                "first_month": first,
                "last_month": last,
                "missing_months": [mo for mo in expected if mo not in counts],
                "multi_file_months": sorted(mo for mo, n in counts.items() if n > 1),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "gse": pl.String,
            "prefix": pl.String,
            "folder": pl.String,
            "files": pl.Int64,
            "first_month": pl.String,
            "last_month": pl.String,
            "missing_months": pl.List(pl.String),
            "multi_file_months": pl.List(pl.String),
        },
    )


def get_manual_download_instructions() -> str:
    """Return formatted download instructions for UMBS data.

    Returns:
        Multi-line string naming every feed A reads, its raw folder, file name and first
        month, and the look-alike files to avoid.

    Example:
        >>> instructions = get_manual_download_instructions()
        >>> print(instructions)
    """
    feed_lines = "\n".join(
        f"  {f.prefix:<8} {f'raw/{f.gse}/{f.folder}/':<23} {f.file_pattern:<23} {f.first_month}"
        for f in RAW_FEEDS
    )
    return f"""
================================================================================
UMBS DATA - MANUAL DOWNLOAD REQUIRED
================================================================================

Automated download is not supported: the GSEs publish these files behind a
registered login or on SFTP.

FANNIE MAE
  Portal: {FANNIE_MAE_PORTAL}
  URL: {FANNIE_MAE_URL}

FREDDIE MAC
  Portal: {FREDDIE_MAC_PORTAL}
  URL: {FREDDIE_MAC_URL}

FEEDS TO DOWNLOAD: every month from the start month, into the folder shown
under data/umbs/, keeping the published file names.

  PREFIX   FOLDER                  FILE                    FROM
{feed_lines}

  RILLD exists only from issuance month 2026-03. RIS and RILLD are posted only
  for months that had corrections, so a missing month there is not a gap.

LOOK-ALIKE FILES (do not substitute):
  - Freddie's FRE_IS_YYYYMM.zip is the issuance security file. The two-letter
    Freddie "IS" + YYMM file (IS2601.zip) is a different feed; it goes in
    raw/FHLMC/IS/, never in FRE_IS/.
  - FNM_MF_YYYYMM is Fannie's Monthly Factor (security-level) file, not
    multifamily data.

REPUBLICATIONS: KEEP THEM
  A GSE sometimes republishes a month (FNM_ILLD_202507_REV.zip,
  FNM_IS_202509_REV.zip, FNM_MF_202108_2.zip, a second fdYYMMDD.zip in one
  month). Keep the republication beside the original; do not overwrite or
  delete either.

AFTER DOWNLOAD:
  $ mortgage-data umbs info      # coverage: missing months, multi-file months
  $ mortgage-data umbs bronze

ABOUT UMBS:
  - Issued since June 2019 (Single Security Initiative)
  - Backed by 30-, 20-, 15-, and 10-year fixed-rate mortgages
  - Same characteristics regardless of issuer (Fannie or Freddie)
  - TBA-eligible securities

================================================================================
"""


def download(*args: object, **kwargs: object) -> None:
    """Raises NotImplementedError with download instructions.

    This function exists to provide a consistent interface across all
    subpackages, but UMBS data requires manual download.

    Args:
        *args: Ignored; accepted for interface parity with other subpackages.
        **kwargs: Ignored; accepted for interface parity with other subpackages.

    Raises:
        NotImplementedError: Always, with instructions for manual download

    Example:
        >>> from mortgage_data_manager.sources.umbs.download import download
        >>> download()  # Raises NotImplementedError
    """
    raise NotImplementedError(get_manual_download_instructions())
