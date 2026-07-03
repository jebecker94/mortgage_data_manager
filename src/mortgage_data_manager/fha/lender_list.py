"""Scrape the HUD FHA-approved Lender List into the medallion pipeline.

HUD publishes its roster of FHA-approved lenders through the Lender List
Search (https://www.hud.gov/hud-partners/single-family-lender-list), backed
by a plain-HTML endpoint at ``apps.hud.gov/ll/code/getllst.cfm``. The
endpoint requires a state filter, so a full national pull iterates over all
states and territories (one request per state; every state's result set fits
in a single page at ``groupsize=5000``).

Each lender record carries HUD's 10-digit ``mort_id``, whose first five
digits are the mortgagee ID — the same identifier as ``Originating
Mortgagee Number`` and ``Sponsor Number`` in the FHA snapshot data (which
stores them as integers, dropping leading zeros).

Layers:
    * Raw: per-state HTML saved to ``raw/lender_list/{snapshot}/``.
    * Bronze: parsed parquet at ``bronze/lender_list/lender_list_{snapshot}.parquet``.

Example:
    >>> from mortgage_data_manager.fha.lender_list import (
    ...     download_lender_list,
    ...     build_bronze_lender_list,
    ... )
    >>> download_lender_list()
    >>> path = build_bronze_lender_list()
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import retry_request
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fha.config import FHAConfig

logger = get_logger(__name__)

LENDER_LIST_ENDPOINT = "https://apps.hud.gov/ll/code/getllst.cfm"

# All states and territories accepted by the Lender List Search form.
LENDER_LIST_STATES: tuple[str, ...] = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "GU",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "PR",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "VI",
    "WA",
    "WV",
    "WI",
    "WY",
)

# Largest state (TX) returns under 1,000 records, so one page covers any state.
DEFAULT_GROUP_SIZE = 5000

DEFAULT_RAW_DIR = FHAConfig.FHA_RAW_DIR / "lender_list"
DEFAULT_BRONZE_DIR = FHAConfig.FHA_BRONZE_DIR / "lender_list"

# Explicit bronze schema (prefer explicit schemas over inference).
BRONZE_SCHEMA: dict[str, type[pl.DataType]] = {
    "lender_name": pl.Utf8,
    "street": pl.Utf8,
    "city": pl.Utf8,
    "state": pl.Utf8,
    "zip_code": pl.Utf8,
    "county": pl.Utf8,
    "title_type": pl.Utf8,
    "approval_date": pl.Date,
    "areas_approved": pl.Int32,
    "mort_id": pl.Utf8,
    "mortgagee_id": pl.Utf8,
    "branch_id": pl.Utf8,
    "ins_type": pl.Utf8,
    "hecm": pl.Boolean,
    "originates_203k": pl.Boolean,
    "telephone": pl.Utf8,
    "fax": pl.Utf8,
    "email": pl.Utf8,
    "source_state": pl.Utf8,
    "snapshot_date": pl.Date,
}

# Mirrors the official search form: ldrtp=05 is a hidden constant, the
# pbox (Title I/II) and sobox (servicer/originator type) checkboxes are all
# checked by default, so this is an unfiltered pull.
_BASE_PARAMS: dict[str, str] = {
    "startseq": "-1",
    "called_by": "llslcrit",
    "ldrtp": "05",
    "lndrnme": "",
    "lndrcity": "",
    "lndrcounty": "",
    "lndrcountyCode": "none",
    "lndrzip": "",
    "ldrad": "1",
    "aafb": "all_areas",
    "pbox1": "on",
    "pbox2": "on",
    "sobox1": "on",
    "sobox2": "on",
    "sobox3": "on",
    "sobox4": "on",
    "sobox5": "on",
    "sobox6": "on",
}

_ANCHOR_PATTERN = re.compile(r'<a name="\d+">')
_MATCH_COUNT_PATTERN = re.compile(r"(\d+) lenders match", re.IGNORECASE)
_NO_MATCH_PATTERN = re.compile(r"NO Lenders match", re.IGNORECASE)
_CITY_STATE_ZIP_PATTERN = re.compile(r"^(.*?),\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")
_COUNTY_PATTERN = re.compile(r"^(.*?)\s*County$")
_MORT_ID_PATTERN = re.compile(r"mort_id=(\d{10})")
_INS_TYPE_PATTERN = re.compile(r"ins_type=(\d)")
_AREAS_PATTERN = re.compile(r"(\d+) Areas Approved for Business")
_TITLE_PATTERN = re.compile(r"\bTitle (II|I)\b")
_APPROVAL_DATE_PATTERN = re.compile(r"Approval Date:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})")
_HECM_PATTERN = re.compile(r"HECM:\s*(Yes|No)")
_203K_PATTERN = re.compile(r"Originates 203K:\s*(Yes|No)")
_TELEPHONE_PATTERN = re.compile(r"Telephone:\s*([\d()\s-]+\d)")
_FAX_PATTERN = re.compile(r"FAX Number:\s*([\d()\s-]+\d)")
_EMAIL_PATTERN = re.compile(r"E-Mail Address:\s*(\S+@\S+)")
_TAG_PATTERN = re.compile(r"<[^>]+>")
_BR_PATTERN = re.compile(r"<br\s*/?>")


def build_lender_list_params(state: str, group_size: int = DEFAULT_GROUP_SIZE) -> dict[str, str]:
    """Build query parameters for one state's Lender List Search request.

    Args:
        state: Two-letter state or territory code (e.g., "CA").
        group_size: Page size. The endpoint honors values far above the
            form's nominal maximum; the default fetches any state in one page.

    Returns:
        Query parameter dict for ``LENDER_LIST_ENDPOINT``.
    """
    params = dict(_BASE_PARAMS)
    params["lndstate"] = state.upper()
    params["groupsize"] = str(group_size)
    return params


def parse_match_count(html: str) -> int | None:
    """Extract the "N lenders match your selection criteria" count.

    Args:
        html: Raw HTML of a Lender List Search results page.

    Returns:
        The advertised match count, 0 for an explicit "NO Lenders match"
        page, or None if neither marker is present (likely an error page).
    """
    match = _MATCH_COUNT_PATTERN.search(html)
    if match:
        return int(match.group(1))
    if _NO_MATCH_PATTERN.search(html):
        return 0
    return None


def _parse_block(block: str, source_state: str) -> dict | None:
    """Parse one lender block (the HTML following an ``<a name="N">`` anchor).

    Args:
        block: HTML fragment for a single lender record.
        source_state: State code used in the originating query.

    Returns:
        Field dict for the record, or None if no lender name is found.
    """
    # The areas-approved link is the only place the mort_id appears.
    mort_id_match = _MORT_ID_PATTERN.search(block)
    mort_id = mort_id_match.group(1) if mort_id_match else None
    ins_type_match = _INS_TYPE_PATTERN.search(block)

    # Flatten to text, preserving <br /> line structure for address parsing.
    text = _BR_PATTERN.sub("\n", block)
    text = _TAG_PATTERN.sub(" ", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]

    if not lines:
        return None

    name = lines[0]

    # Address: lines after the name up to the "CITY,  ST  ZIP" line are the
    # street; the line after the ZIP names the county (or is bare "County").
    street_lines: list[str] = []
    city = state = zip_code = county = None
    for i, line in enumerate(lines[1:], start=1):
        csz = _CITY_STATE_ZIP_PATTERN.match(line)
        if csz:
            city, state, zip_code = csz.group(1).strip(), csz.group(2), csz.group(3)
            if i + 1 < len(lines):
                county_match = _COUNTY_PATTERN.match(lines[i + 1])
                if county_match:
                    county = county_match.group(1).strip() or None
            break
        if "Approval Date" in line or "Title " in line:
            break
        street_lines.append(line)

    joined = " ".join(lines)
    title_match = _TITLE_PATTERN.search(joined)
    approval_match = _APPROVAL_DATE_PATTERN.search(joined)
    areas_match = _AREAS_PATTERN.search(joined)
    hecm_match = _HECM_PATTERN.search(joined)
    k203_match = _203K_PATTERN.search(joined)
    phone_match = _TELEPHONE_PATTERN.search(joined)
    fax_match = _FAX_PATTERN.search(joined)
    email_match = _EMAIL_PATTERN.search(joined)

    approval_date = None
    if approval_match:
        approval_date = datetime.strptime(approval_match.group(1), "%b %d, %Y").date()

    if areas_match:
        areas_approved = int(areas_match.group(1))
    elif "No Areas Approved for Business" in joined:
        areas_approved = 0
    else:
        areas_approved = None

    return {
        "lender_name": name,
        "street": "; ".join(street_lines) or None,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "county": county,
        "title_type": f"Title {title_match.group(1)}" if title_match else None,
        "approval_date": approval_date,
        "areas_approved": areas_approved,
        "mort_id": mort_id,
        "mortgagee_id": mort_id[:5] if mort_id else None,
        "branch_id": mort_id[5:] if mort_id else None,
        "ins_type": ins_type_match.group(1) if ins_type_match else None,
        "hecm": hecm_match.group(1) == "Yes" if hecm_match else None,
        "originates_203k": k203_match.group(1) == "Yes" if k203_match else None,
        "telephone": phone_match.group(1).strip() if phone_match else None,
        "fax": fax_match.group(1).strip() if fax_match else None,
        "email": email_match.group(1).strip() if email_match else None,
        "source_state": source_state,
    }


def parse_lender_list_html(html: str, source_state: str) -> list[dict]:
    """Parse all lender records from one results page.

    Args:
        html: Raw HTML of a Lender List Search results page.
        source_state: State code used in the originating query.

    Returns:
        List of record dicts (one per lender entry on the page).
    """
    blocks = _ANCHOR_PATTERN.split(html)
    records = []
    for block in blocks[1:]:
        record = _parse_block(block, source_state)
        if record is not None:
            records.append(record)
    return records


def download_lender_list(
    states: list[str] | tuple[str, ...] | None = None,
    snapshot_date: str | None = None,
    *,
    destination: Path | str | None = None,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    overwrite: bool = False,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> list[Path]:
    """Download raw Lender List Search HTML for each state.

    Saves one HTML file per state to ``raw/lender_list/{snapshot_date}/``.
    The roster is a point-in-time snapshot of currently approved lenders, so
    files are grouped under a dated directory rather than overwritten.

    Args:
        states: State codes to fetch. Defaults to all states and territories.
        snapshot_date: Snapshot label in YYYYMMDD form. Defaults to today.
        destination: Root raw directory. Defaults to ``raw/lender_list``.
        pause: Seconds between requests. Defaults to config DOWNLOAD_PAUSE.
        timeout: Request timeout in seconds.
        retries: Number of retry attempts per request.
        overwrite: If True, re-download files that already exist.
        group_size: Page size per request.

    Returns:
        List of paths to the saved HTML files (existing files included).

    Raises:
        ValueError: If a state code is not recognized.
    """
    import time

    states = tuple(s.upper() for s in (states or LENDER_LIST_STATES))
    unknown = sorted(set(states) - set(LENDER_LIST_STATES))
    if unknown:
        raise ValueError(f"Unknown state codes: {unknown}")

    snapshot_date = snapshot_date or date.today().strftime("%Y%m%d")
    root = Path(destination) if destination else DEFAULT_RAW_DIR
    snapshot_dir = (root / snapshot_date).resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    pause = pause if pause is not None else MortgageDataConfig.DOWNLOAD_PAUSE
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}

    saved: list[Path] = []
    for i, state in enumerate(states):
        filepath = snapshot_dir / f"lender_list_{state}.html"
        if filepath.exists() and not overwrite:
            logger.debug("Skipping %s (already exists)", filepath.name)
            saved.append(filepath)
            continue

        logger.info("Downloading lender list for %s (%d/%d)", state, i + 1, len(states))
        params = build_lender_list_params(state, group_size=group_size)
        url = f"{LENDER_LIST_ENDPOINT}?{urlencode(params)}"
        try:
            response = retry_request(
                url,
                timeout=timeout,
                retries=retries,
                headers=headers,
            )
        except Exception as e:
            logger.error("Failed to download lender list for %s: %s", state, e)
            continue

        count = parse_match_count(response.text)
        if count is None:
            logger.error(
                "Response for %s has no match-count marker (error page?); not saving", state
            )
            continue

        filepath.write_text(response.text, encoding="utf-8")
        logger.info("Saved %s (%d lenders advertised)", filepath.name, count)
        saved.append(filepath)

        if i < len(states) - 1:
            time.sleep(pause)

    return saved


def find_latest_snapshot(raw_dir: Path | str | None = None) -> Path:
    """Locate the most recent raw lender list snapshot directory.

    Args:
        raw_dir: Root raw directory. Defaults to ``raw/lender_list``.

    Returns:
        Path to the latest ``YYYYMMDD``-named snapshot directory.

    Raises:
        FileNotFoundError: If no snapshot directories exist.
    """
    root = Path(raw_dir) if raw_dir else DEFAULT_RAW_DIR
    snapshots = sorted(
        (p for p in root.glob("[0-9]" * 8) if p.is_dir()),
        key=lambda p: p.name,
    )
    if not snapshots:
        raise FileNotFoundError(f"No lender list snapshots found under {root}")
    return snapshots[-1]


def build_bronze_lender_list(
    snapshot_date: str | None = None,
    *,
    raw_dir: Path | str | None = None,
    bronze_dir: Path | str | None = None,
    overwrite: bool = False,
) -> Path:
    """Parse raw lender list HTML into a bronze parquet file.

    Validates the parsed record count for each state against the page's own
    "N lenders match" marker and logs any discrepancy.

    Args:
        snapshot_date: Snapshot label (YYYYMMDD). Defaults to the latest
            snapshot directory found under the raw root.
        raw_dir: Root raw directory. Defaults to ``raw/lender_list``.
        bronze_dir: Output directory. Defaults to ``bronze/lender_list``.
        overwrite: If True, rebuild even when the output file exists.

    Returns:
        Path to the bronze parquet file.

    Raises:
        FileNotFoundError: If the snapshot directory or its HTML files are missing.
    """
    root = Path(raw_dir) if raw_dir else DEFAULT_RAW_DIR
    snapshot_dir = (root / snapshot_date) if snapshot_date else find_latest_snapshot(root)
    if not snapshot_dir.is_dir():
        raise FileNotFoundError(f"Snapshot directory not found: {snapshot_dir}")
    snapshot = snapshot_dir.name

    out_dir = Path(bronze_dir) if bronze_dir else DEFAULT_BRONZE_DIR
    out_path = (out_dir / f"lender_list_{snapshot}.parquet").resolve()
    if not should_process_file(out_path, overwrite):
        logger.info("Bronze lender list already exists: %s (use overwrite=True)", out_path)
        return out_path

    html_files = sorted(snapshot_dir.glob("lender_list_*.html"))
    if not html_files:
        raise FileNotFoundError(f"No lender list HTML files in {snapshot_dir}")

    snapshot_dt = datetime.strptime(snapshot, "%Y%m%d").date()
    records: list[dict] = []
    for filepath in html_files:
        state = filepath.stem.removeprefix("lender_list_")
        html = filepath.read_text(encoding="utf-8")
        state_records = parse_lender_list_html(html, source_state=state)

        advertised = parse_match_count(html)
        if advertised is not None and advertised != len(state_records):
            logger.warning(
                "%s: parsed %d records but page advertised %d",
                state,
                len(state_records),
                advertised,
            )
        for record in state_records:
            record["snapshot_date"] = snapshot_dt
        records.extend(state_records)

    df = pl.DataFrame(records, schema=BRONZE_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    logger.info(
        "Wrote %d lender records (%d unique mortgagee IDs) to %s",
        len(df),
        df["mortgagee_id"].drop_nulls().n_unique(),
        out_path,
    )
    return out_path
