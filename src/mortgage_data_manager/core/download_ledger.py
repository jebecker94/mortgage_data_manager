"""Append-only ledger of raw data acquisitions.

Every file that enters a raw layer — fetched by `core.download`, saved from
a JSON API, extracted from an archive, or placed by hand and registered — gets
one `DownloadRecord` appended to ``{DOWNLOAD_LEDGER_DIR}/{source}.jsonl``, as
does every failed fetch. A skip (the file is already on disk) is not recorded.

The ledger directory covers this package's sources only. A package built on
this kernel keeps its own ledger by calling `register_ledger` for its sources;
their records then go to that package's directory, stamped with its name and
version.
The ledger is history, not state: a re-download of the same destination with a
different ``sha256`` is a first-class event ("the source revised this file"),
which `changes` surfaces.

Records carry a content hash and a magic-byte content type, so "downloaded"
means "these bytes, of this kind" rather than "a request returned 200". The
``destination`` is always relative to the source's raw directory — never an
absolute path — so ledgers are portable across machines and volumes.

Example:
    >>> from mortgage_data_manager.core.download_ledger import latest, changes
    >>> latest("fha").select("destination", "last_modified", "sha256")
    >>> changes("gnma")  # destinations whose bytes differed between two acquisitions
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

PACKAGE = "mortgage_data_manager"

AcquisitionMethod = Literal["http", "api_json", "extracted", "manual", "backfill"]
"""How the bytes arrived: fetched by core, a JSON API body, an archive member,
placed by hand, or the one-time baseline registration."""

SNIFF_HEAD_BYTES = 512
"""Bytes of a file's head that `sniff_type` inspects."""

#: Names never treated as raw data by `register_files` / `unrecorded_files`.
IGNORED_NAMES: frozenset[str] = frozenset({".DS_Store", ".gitkeep", ".bulk_downloads"})

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty archive
    (b"MZ", "sfx_zip"),  # self-extracting archive; zipfile opens it via the trailing directory
    (b"\x1f\x8b", "gzip"),
    (b"%PDF", "pdf"),
    (b"\xd0\xcf\x11\xe0", "xls"),  # OLE2 compound document (xls / doc)
    (b"PAR1", "parquet"),
    (b"7z\xbc\xaf", "7z"),
)

LEDGER_SCHEMA: dict[str, pl.DataType] = {
    "recorded_at": pl.String(),
    "run_id": pl.String(),
    "argv": pl.String(),
    "package": pl.String(),
    "package_version": pl.String(),
    "source": pl.String(),
    "dataset": pl.String(),
    "method": pl.String(),
    "url": pl.String(),
    "final_url": pl.String(),
    "destination": pl.String(),
    "parent": pl.String(),
    "status": pl.String(),
    "http_status": pl.Int64(),
    "bytes": pl.Int64(),
    "content_length": pl.Int64(),
    "sha256": pl.String(),
    "sniffed_type": pl.String(),
    "etag": pl.String(),
    "last_modified": pl.String(),
    "content_type": pl.String(),
    "started_at": pl.String(),
    "duration_s": pl.Float64(),
    "attempts": pl.Int64(),
    "error": pl.String(),
    "note": pl.String(),
}
"""Column types of a ledger; the field order of `DownloadRecord`."""

_RUN_ID: str | None = None


def run_id() -> str:
    """Return this process's run id, minted on first use.

    Returns:
        The run id shared by every record this process appends.
    """
    global _RUN_ID  # noqa: PLW0603 - one id per process is the point
    if _RUN_ID is None:
        _RUN_ID = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    return _RUN_ID


def _package_version() -> str:
    from mortgage_data_manager import __version__

    return __version__


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _argv() -> str:
    """The invoking command line, with the interpreter/script path reduced to its name."""
    if not sys.argv:
        return ""
    return " ".join([Path(sys.argv[0]).name, *sys.argv[1:]])


@dataclass
class DownloadRecord:
    """One acquisition event. Field names are settled; see the module docstring."""

    source: str
    destination: str
    method: AcquisitionMethod
    status: str
    dataset: str | None = None
    url: str | None = None
    final_url: str | None = None
    parent: str | None = None
    http_status: int | None = None
    bytes: int | None = None
    content_length: int | None = None
    sha256: str | None = None
    sniffed_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    started_at: str | None = None
    duration_s: float | None = None
    attempts: int | None = None
    error: str | None = None
    note: str | None = None
    recorded_at: str = field(default_factory=_now)
    run_id: str = field(default_factory=run_id)
    argv: str = field(default_factory=lambda: _argv())
    package: str = PACKAGE
    package_version: str = field(default_factory=_package_version)

    def to_json(self) -> str:
        """Serialize in `LEDGER_SCHEMA` column order.

        Returns:
            The record as a single JSON line.
        """
        d = asdict(self)
        return json.dumps({k: d[k] for k in LEDGER_SCHEMA}, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------


def ledger_dir() -> Path:
    """The ledger directory (read at call time so tests can repoint it).

    Returns:
        The configured ledger directory.
    """
    return MortgageDataConfig.DOWNLOAD_LEDGER_DIR


@dataclass(frozen=True)
class _ExternalLedger:
    """Where another package's sources are ledgered, and how they are stamped."""

    package: str
    version: str
    directory: Callable[[], Path]


_EXTERNAL_LEDGERS: dict[str, _ExternalLedger] = {}


def register_ledger(
    package: str, version: str, sources: Iterable[str], directory: Callable[[], Path]
) -> None:
    """Send another package's sources to that package's own ledger directory.

    Call once at import time from the package's base config, so no record for one
    of its sources can reach this package's ledger.

    Args:
        package: The registering distribution's import name (stamped on records).
        version: Its version (stamped on records).
        sources: Source names the package ledgers under.
        directory: Zero-arg resolver of the package's ledger directory, read at
            write time so tests can repoint it.

    Raises:
        ValueError: If a source is already registered by a different package.
    """
    entry = _ExternalLedger(package=package, version=version, directory=directory)
    for source in sources:
        existing = _EXTERNAL_LEDGERS.get(source)
        if existing is not None and existing.package != package:
            raise ValueError(f"source {source!r} is already ledgered by {existing.package}")
        _EXTERNAL_LEDGERS[source] = entry


def ledger_path(source: str) -> Path:
    """Path of one source's ledger file.

    Args:
        source: Source name.

    Returns:
        The ``{source}.jsonl`` path in this package's ledger directory, or in the
        registering package's directory for a source passed to `register_ledger`.
    """
    external = _EXTERNAL_LEDGERS.get(source)
    directory = external.directory() if external is not None else ledger_dir()
    return directory / f"{source}.jsonl"


def relative_destination(path: Path, raw_dir: Path | None) -> str:
    """Express ``path`` relative to ``raw_dir`` with POSIX separators.

    Falls back to the path relative to ``MORTGAGE_DATA_DIR`` when ``raw_dir`` is
    unknown or does not contain ``path``, and to the bare filename as a last resort
    — a ledger must never carry an absolute path.

    Args:
        path: The destination path to express.
        raw_dir: The source's raw root, or None when it is unknown.

    Returns:
        A relative POSIX-separated destination string.
    """
    path = Path(path)
    candidates = [raw_dir, MortgageDataConfig.DATA_DIR]
    for root in candidates:
        if root is None:
            continue
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            continue
    return path.name


# -----------------------------------------------------------------------------
# Content inspection
# -----------------------------------------------------------------------------


def sniff_type(head: bytes) -> str:
    """Classify a file by its first bytes.

    Deliberately not extension-aware: the point is to catch a body that does not
    match its name. xlsx files report as ``zip``.

    Args:
        head: The first bytes of the file.

    Returns:
        One of ``zip``, ``sfx_zip``, ``gzip``, ``pdf``, ``xls``, ``parquet``,
        ``7z``, ``html``, ``xml``, ``json``, ``text``, ``empty`` or ``unknown``.
    """
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    stripped = head.lstrip()
    if not stripped:
        return "empty"
    low = stripped[:64].lower()
    if low.startswith((b"<!doctype", b"<html")):
        return "html"
    if low.startswith(b"<?xml"):
        return "html" if b"<html" in stripped.lower() else "xml"
    if low.startswith((b"{", b"[")):
        return "json"
    try:
        stripped.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        pass
    # Latin-1 decodes anything, so control-character density is the tell:
    # printable ASCII, whitespace and the Latin-1 upper half count as text.
    textual = sum(32 <= b < 127 or b >= 0xA0 or b in (9, 10, 13) for b in stripped)
    return "text" if textual / len(stripped) > 0.95 else "unknown"


class StreamInspector:
    """Feed download chunks through this to get the hash, size and sniffed type."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self._head = bytearray()
        self.bytes = 0

    def update(self, chunk: bytes) -> None:
        """Consume one chunk.

        Args:
            chunk: The next bytes of the stream.
        """
        self._hash.update(chunk)
        if len(self._head) < SNIFF_HEAD_BYTES:
            self._head.extend(chunk[: SNIFF_HEAD_BYTES - len(self._head)])
        self.bytes += len(chunk)

    @property
    def sha256(self) -> str:
        """Hex digest of everything consumed so far."""
        return self._hash.hexdigest()

    @property
    def sniffed_type(self) -> str:
        """Content type of the head consumed so far."""
        return sniff_type(bytes(self._head))


def inspect_file(path: Path, chunk_size: int = 2**20) -> StreamInspector:
    """Hash and sniff an existing file (one sequential pass).

    Args:
        path: The file to inspect.
        chunk_size: Read size in bytes.

    Returns:
        The inspector carrying the file's digest, sniffed type and size.
    """
    inspector = StreamInspector()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            inspector.update(chunk)
    return inspector


# -----------------------------------------------------------------------------
# Writing
# -----------------------------------------------------------------------------


def append_record(record: DownloadRecord) -> Path:
    """Append one record to its source's ledger.

    Args:
        record: The record to append.

    Returns:
        The ledger path written to.
    """
    external = _EXTERNAL_LEDGERS.get(record.source)
    if external is not None:
        record = replace(record, package=external.package, package_version=external.version)
    path = ledger_path(record.source)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (record.to_json() + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return path


# -----------------------------------------------------------------------------
# Reading
# -----------------------------------------------------------------------------


def read_ledger(source: str | None = None) -> pl.LazyFrame:
    """Scan one source's ledger, or every ledger when ``source`` is None.

    Malformed lines are skipped with a warning rather than failing the read. An
    absent ledger yields an empty frame with the full schema. Ledgers are small
    (about 1 KB per record), so the whole file is parsed eagerly.

    Args:
        source: Source to read, or None for every ledger.

    Returns:
        A lazy frame on `LEDGER_SCHEMA`.
    """
    if source is not None:
        paths = [ledger_path(source)]
    else:
        paths = sorted(ledger_dir().glob("*.jsonl")) if ledger_dir().exists() else []
    rows: list[dict[str, object]] = []
    for path in paths:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("%s:%d: malformed ledger line skipped", path.name, lineno)
    if not rows:
        return pl.LazyFrame(schema=LEDGER_SCHEMA)
    return pl.from_dicts(rows, schema=LEDGER_SCHEMA).lazy()


_PRESENT_STATUSES = ("success", "registered")


def latest(source: str | None = None) -> pl.DataFrame:
    """The last successful or registered record per (source, destination).

    Args:
        source: Source to read, or None for every ledger.

    Returns:
        One row per ``(source, destination)``, carrying its latest record.
    """
    return (
        read_ledger(source)
        .filter(pl.col("status").is_in(_PRESENT_STATUSES))
        .sort("recorded_at")
        .group_by("source", "destination", maintain_order=True)
        .last()
        .collect()
    )


def changes(source: str | None = None) -> pl.DataFrame:
    """Destinations acquired more than once with differing ``sha256``.

    Args:
        source: Source to read, or None for every ledger.

    Returns:
        One row per ``(source, destination, sha256)`` in acquisition order, so a
        reader sees when each revision arrived and from which run.
    """
    lf = (
        read_ledger(source)
        .filter(pl.col("status").is_in(_PRESENT_STATUSES) & pl.col("sha256").is_not_null())
        .sort("recorded_at")
    )
    versions = lf.group_by("source", "destination").agg(n_versions=pl.col("sha256").n_unique())
    return (
        lf.join(versions.filter(pl.col("n_versions") > 1), on=["source", "destination"])
        .unique(subset=["source", "destination", "sha256"], keep="first", maintain_order=True)
        .select(
            "source", "destination", "recorded_at", "sha256", "bytes", "method", "run_id", "argv"
        )
        .sort("source", "destination", "recorded_at")
        .collect()
    )


# -----------------------------------------------------------------------------
# Registration and audit of files already on disk
# -----------------------------------------------------------------------------


def _is_raw_file(path: Path) -> bool:
    return path.is_file() and path.name not in IGNORED_NAMES and not path.name.startswith(".")


def iter_raw_files(raw_dir: Path) -> list[Path]:
    """Every regular data file under ``raw_dir`` (dot-files and markers excluded).

    Args:
        raw_dir: Directory to walk.

    Returns:
        The regular data files found, sorted.
    """
    return sorted(p for p in Path(raw_dir).rglob("*") if _is_raw_file(p))


def unrecorded_files(source: str, raw_dir: Path) -> list[str]:
    """Destinations under ``raw_dir`` with no successful/registered ledger record.

    A file whose size differs from its latest record is also reported: the bytes
    on disk are not the bytes the ledger knows.

    Args:
        source: Source whose ledger to check against.
        raw_dir: The source's raw root.

    Returns:
        The unrecorded or size-mismatched destinations.
    """
    current = latest(source).select("destination", "bytes")
    known = dict(zip(current["destination"], current["bytes"], strict=True))
    missing = []
    for path in iter_raw_files(raw_dir):
        dest = relative_destination(path, raw_dir)
        if known.get(dest) != path.stat().st_size:
            missing.append(dest)
    return missing


def register_files(
    source: str,
    raw_dir: Path,
    *,
    method: Literal["manual", "backfill"] = "manual",
    dataset: str | None = None,
    note: str | None = None,
    extra: dict[str, dict[str, str]] | None = None,
) -> int:
    """Record every unrecorded file under ``raw_dir`` (hashing each one).

    Resumable: files whose latest record already matches their size are skipped,
    so an interrupted registration continues where it stopped.

    Args:
        source: Subpackage name the files belong to.
        raw_dir: Directory the ``destination`` paths are expressed relative to.
        method: ``manual`` for hand-placed files, ``backfill`` for the one-time
            baseline of a raw layer that predates the ledger.
        dataset: Optional dataset tag applied to every record.
        note: Optional free-text provenance applied to every record.
        extra: Optional per-destination ``{"url": …, "last_modified": …}`` values
            (used to carry a legacy manifest into the ledger).

    Returns:
        Number of records written.
    """
    pending = unrecorded_files(source, raw_dir)
    logger.info("%s: %d file(s) to register under %s", source, len(pending), raw_dir)
    written = 0
    for dest in pending:
        path = Path(raw_dir) / dest
        started = _now()
        t0 = datetime.now(UTC)
        info = inspect_file(path)
        meta = (extra or {}).get(dest, {})
        append_record(
            DownloadRecord(
                source=source,
                destination=dest,
                method=method,
                status="registered",
                dataset=dataset,
                url=meta.get("url"),
                last_modified=meta.get("last_modified"),
                bytes=info.bytes,
                sha256=info.sha256,
                sniffed_type=info.sniffed_type,
                started_at=started,
                duration_s=(datetime.now(UTC) - t0).total_seconds(),
                note=note,
            )
        )
        written += 1
        logger.info(
            "registered %s/%s (%s, %s bytes)", source, dest, info.sniffed_type, f"{info.bytes:,}"
        )
    return written


__all__ = [
    "IGNORED_NAMES",
    "LEDGER_SCHEMA",
    "AcquisitionMethod",
    "DownloadRecord",
    "StreamInspector",
    "append_record",
    "changes",
    "inspect_file",
    "iter_raw_files",
    "latest",
    "ledger_dir",
    "ledger_path",
    "read_ledger",
    "register_files",
    "register_ledger",
    "relative_destination",
    "run_id",
    "sniff_type",
    "unrecorded_files",
]
