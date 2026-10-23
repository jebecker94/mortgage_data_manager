"""Unit tests for the raw download ledger (pure logic: sniffing, records, readers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download_ledger import (
    LEDGER_SCHEMA,
    DownloadRecord,
    StreamInspector,
    append_record,
    changes,
    inspect_file,
    latest,
    read_ledger,
    register_files,
    relative_destination,
    sniff_type,
    unrecorded_files,
)


@pytest.fixture
def ledger_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "ledger"
    monkeypatch.setattr(MortgageDataConfig, "DOWNLOAD_LEDGER_DIR", d)
    return d


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"PK\x03\x04rest", "zip"),
        (b"PK\x05\x06", "zip"),
        (b"MZP\x00\x02\x00 This program must be run under Win32", "sfx_zip"),
        (b"\x1f\x8b\x08", "gzip"),
        (b"%PDF-1.7", "pdf"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xls"),
        (b"PAR1", "parquet"),
        (b"  <!DOCTYPE html><html>", "html"),
        (b"<html><head>", "html"),
        (b'<?xml version="1.0"?><root/>', "xml"),
        (b'<?xml version="1.0"?><html>', "html"),
        (b'{"data": {"results": []}}', "json"),
        (b"[1, 2]", "json"),
        (b"col1,col2\n1,2\n", "text"),
        (b"", "empty"),
        (b"   \n", "empty"),
        (bytes(range(256)), "unknown"),
    ],
)
def test_sniff_type(head: bytes, expected: str) -> None:
    assert sniff_type(head) == expected


def test_sniff_latin1_text_is_text() -> None:
    assert sniff_type("Städte,Straße\n1,2\n".encode("latin-1")) == "text"


def test_stream_inspector_matches_whole_file(tmp_path: Path) -> None:
    payload = b"PK\x03\x04" + bytes(range(256)) * 40
    f = tmp_path / "x.zip"
    f.write_bytes(payload)
    streamed = StreamInspector()
    for i in range(0, len(payload), 7):
        streamed.update(payload[i : i + 7])
    whole = inspect_file(f)
    assert streamed.sha256 == whole.sha256
    assert streamed.bytes == whole.bytes == len(payload)
    assert streamed.sniffed_type == whole.sniffed_type == "zip"


def test_relative_destination_never_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MortgageDataConfig, "DATA_DIR", tmp_path / "data")
    raw = tmp_path / "data" / "hmda" / "raw"
    assert relative_destination(raw / "loans" / "a.zip", raw) == "loans/a.zip"
    assert relative_destination(raw / "loans" / "a.zip", None) == "hmda/raw/loans/a.zip"
    assert relative_destination(Path("/elsewhere/b.zip"), raw) == "b.zip"


def test_record_round_trip(ledger_dir: Path) -> None:
    rec = DownloadRecord(
        source="test", destination="a/b.zip", method="http", status="success", bytes=3, sha256="ab"
    )
    path = append_record(rec)
    assert path == ledger_dir / "test.jsonl"
    line = json.loads(path.read_text().splitlines()[0])
    assert list(line) == list(LEDGER_SCHEMA)
    assert line["destination"] == "a/b.zip"
    assert line["package"] == "mortgage_data_manager"
    df = read_ledger("test").collect()
    assert df.height == 1
    assert df.schema == LEDGER_SCHEMA


def test_read_ledger_skips_malformed_lines_and_missing_files(ledger_dir: Path) -> None:
    assert read_ledger("nothing").collect().is_empty()
    append_record(DownloadRecord(source="t", destination="x", method="http", status="success"))
    with open(ledger_dir / "t.jsonl", "a") as f:
        f.write("{not json\n")
    append_record(DownloadRecord(source="t", destination="y", method="http", status="success"))
    assert read_ledger("t").collect().height == 2
    assert read_ledger(None).collect().height == 2


def _rec(
    dest: str, sha: str | None, status: str = "success", when: str = "2026-01-01T00:00:00"
) -> None:
    append_record(
        DownloadRecord(
            source="s",
            destination=dest,
            method="http",
            status=status,
            sha256=sha,
            bytes=1,
            recorded_at=when,
        )
    )


def test_latest_and_changes(ledger_dir: Path) -> None:
    _rec("a.zip", "111", when="2026-01-01T00:00:00")
    _rec("a.zip", None, status="skipped_exists", when="2026-01-02T00:00:00")
    _rec("a.zip", "222", when="2026-01-03T00:00:00")
    _rec("a.zip", "222", when="2026-01-04T00:00:00")  # same bytes again: not a change
    _rec("b.zip", "333", when="2026-01-01T00:00:00")
    _rec("c.zip", None, status="failed", when="2026-01-01T00:00:00")

    lat = latest("s").sort("destination")
    assert lat["destination"].to_list() == ["a.zip", "b.zip"]
    assert lat.filter(lat["destination"] == "a.zip")["sha256"][0] == "222"

    ch = changes("s")
    assert ch["destination"].to_list() == ["a.zip", "a.zip"]
    assert ch["sha256"].to_list() == ["111", "222"]


def test_register_and_audit_are_resumable(ledger_dir: Path, tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "sub").mkdir(parents=True)
    (raw / "sub" / "a.zip").write_bytes(b"PK\x03\x04data")
    (raw / "b.txt").write_text("hello")
    (raw / ".DS_Store").write_bytes(b"junk")
    (raw / ".bulk_downloads").write_text("a.zip\n")

    assert sorted(unrecorded_files("s", raw)) == ["b.txt", "sub/a.zip"]
    assert register_files("s", raw, method="backfill", note="baseline") == 2
    assert unrecorded_files("s", raw) == []
    assert register_files("s", raw, method="backfill") == 0  # nothing left to do

    rows = latest("s").sort("destination")
    assert rows["method"].to_list() == ["backfill", "backfill"]
    assert rows["sniffed_type"].to_list() == ["text", "zip"]
    assert rows["note"].to_list() == ["baseline", "baseline"]

    # A file whose bytes changed on disk is unrecorded again, and registering it is a change.
    (raw / "b.txt").write_text("hello again")
    assert unrecorded_files("s", raw) == ["b.txt"]
    register_files("s", raw)
    assert changes("s")["destination"].to_list() == ["b.txt", "b.txt"]


def test_registered_source_goes_to_its_own_ledger(
    ledger_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mortgage_data_manager.core import download_ledger

    monkeypatch.setattr(download_ledger, "_EXTERNAL_LEDGERS", {})
    other = tmp_path / "other_ledger"
    download_ledger.register_ledger("other_pkg", "9.9", ["ext"], lambda: other)
    append_record(
        DownloadRecord(source="ext", destination="a.zip", method="http", status="success")
    )
    append_record(
        DownloadRecord(source="own", destination="b.zip", method="http", status="success")
    )

    assert not (ledger_dir / "ext.jsonl").exists()
    row = json.loads((other / "ext.jsonl").read_text())
    assert (row["package"], row["package_version"]) == ("other_pkg", "9.9")
    assert json.loads((ledger_dir / "own.jsonl").read_text())["package"] == "mortgage_data_manager"


def test_register_ledger_rejects_a_second_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from mortgage_data_manager.core import download_ledger

    monkeypatch.setattr(download_ledger, "_EXTERNAL_LEDGERS", {})
    download_ledger.register_ledger("pkg_a", "1", ["shared"], lambda: Path("a"))
    with pytest.raises(ValueError, match="already ledgered by pkg_a"):
        download_ledger.register_ledger("pkg_b", "1", ["shared"], lambda: Path("b"))


def test_skip_is_not_recorded(ledger_dir: Path, tmp_path: Path) -> None:
    from mortgage_data_manager.core.download import DownloadStatus, conditional_download

    existing = tmp_path / "raw" / "x.zip"
    existing.parent.mkdir()
    existing.write_bytes(b"PK\x03\x04")
    result = conditional_download(
        "https://example.invalid/x.zip", existing, mode="skip", source="own"
    )
    assert result.status == DownloadStatus.SKIPPED_EXISTS
    assert not (ledger_dir / "own.jsonl").exists()
