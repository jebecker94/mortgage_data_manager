"""Structural guard: raw acquisition goes through ``core.download``.

Every fetch that lands in a raw layer must pass through ``atomic_download`` so it
is hashed, sniffed and written to the raw download ledger. This test greps the
package for direct ``requests``/``urllib`` calls and fails on any file not on the
explicit allow-list below. Add a file to the list only with a reason: the call is
not a raw acquisition (a probe, an auth handshake, a page scrape, a reference
cache) or it records to the ledger itself.

Runs without the data volume.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "mortgage_data_manager"

_DIRECT_CALL = re.compile(
    r"\brequests\.(get|post|head|put|request|Session)\(|\burlretrieve\(|\burlopen\(|\bhttpx\."
)

#: file (relative to the package) -> why a direct HTTP call is acceptable there.
ALLOWED: dict[str, str] = {
    "core/download.py": "the chokepoint itself",
    "sources/usps_crosswalk/download.py": "JSON API where 404 = not published; records to the ledger itself",
    "sources/hmda/download.py": "HEAD publication probe only; bytes go through batch_download",
    "sources/gnma/download.py": "authenticated Session for login/listing; bytes go through atomic_download",
    "core/reference/macro.py": "FRED series cache, not a raw layer",
    "core/geocoding/providers.py": "geocoder API calls, not a raw layer",
    "matching/match_hmda_fha/validation.py": "fetches a public GeoJSON for a validation figure, not a raw layer",
}


def _offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        lines = [
            i
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _DIRECT_CALL.search(line) and not line.lstrip().startswith("#")
        ]
        if lines:
            found[rel] = lines
    return found


def test_no_direct_http_outside_core_download() -> None:
    offenders = _offenders()
    assert not offenders, (
        "Direct HTTP calls outside core.download (route them through atomic_download or add "
        f"to ALLOWED with a reason): {offenders}"
    )


def test_allow_list_entries_still_exist_and_still_call_http() -> None:
    """An allow-list row for a file that no longer makes the call is stale — drop it."""
    stale = []
    for rel in ALLOWED:
        path = SRC / rel
        if not path.exists() or not _DIRECT_CALL.search(path.read_text(encoding="utf-8")):
            stale.append(rel)
    assert not stale, f"Stale ALLOWED entries: {stale}"
