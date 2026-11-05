"""Regression tests: GNMA bronze/silver failures are counted, never reported as success.

Bronze used to drop a ZIP member that failed to read and publish the rest as a whole file,
and swallowed errors while cleaning up; silver logged a failed file and moved on. Each test
injects one failure and asserts it reaches the result counts, the pipeline status and the
CLI exit code, and that no partial file is left under the final name.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from mortgage_data_manager.sources.gnma import cli as gnma_cli
from mortgage_data_manager.sources.gnma import import_silver
from mortgage_data_manager.sources.gnma.config import ProcessorConfig
from mortgage_data_manager.sources.gnma.import_bronze import (
    BronzeImportError,
    import_prefix_bronze,
    import_zip_to_parquet,
)
from mortgage_data_manager.sources.gnma.import_silver import import_prefix_silver_canonical
from mortgage_data_manager.sources.gnma.pipeline import run_processing


def _zip_with_bad_second_member(path: Path) -> None:
    """A stored ZIP whose second member fails its CRC check when read."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("a.txt", "HH|202607|MO|20260807\r\nLL|1\r\n")
        z.writestr("b.txt", "LL|2\r\nTT|202607|2\r\n")
    data = bytearray(path.read_bytes())
    at = data.index(b"LL|2")
    data[at] = ord("X")  # payload changes, stored CRC does not
    path.write_bytes(bytes(data))


# The repo's schemas, not GNMAConfig's: another test reloads the config module with
# repointed environment variables.
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "gnma"


def _config(root: Path, **kw) -> ProcessorConfig:
    return ProcessorConfig(
        raw_folder=root / "raw",
        bronze_folder=root / "bronze",
        silver_folder=root / "silver",
        schemas_folder=SCHEMAS,
        prefix_file=SCHEMAS / "prefix_dictionary.yaml",
        **kw,
    )


def test_bad_zip_member_fails_the_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "llmon"
    raw.mkdir(parents=True)
    _zip_with_bad_second_member(raw / "llmon_202607.zip")

    result = import_prefix_bronze("llmon", tmp_path / "raw", config=_config(tmp_path))

    assert (result.total, result.successful, result.failed) == (1, 0, 1)
    assert "BadZipFile" in result.errors[0]["error"]
    out_dir = tmp_path / "bronze" / "llmon"
    assert not out_dir.exists() or not any(out_dir.iterdir())  # nothing, not even a .tmp


def test_good_zip_converts_every_member(tmp_path: Path) -> None:
    zpath = tmp_path / "llmon_202607.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("a.txt", "HH|202607|MO|20260807\r\nLL|1\r\n")
        z.writestr("b.txt", "LL|2\r\nTT|202607|2\r\n")
    out = tmp_path / "out.parquet"

    assert import_zip_to_parquet(zpath, out, _config(tmp_path, chunk_size=2))
    lines = pl.read_parquet(out)["text_content"].to_list()
    assert lines == ["HH|202607|MO|20260807", "LL|1", "LL|2", "TT|202607|2"]


def test_zip_without_text_member_raises(tmp_path: Path) -> None:
    zpath = tmp_path / "llmon_202607.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("readme.pdf", b"%PDF")
    with pytest.raises(BronzeImportError):
        import_zip_to_parquet(zpath, tmp_path / "out.parquet", _config(tmp_path))
    assert not (tmp_path / "out.parquet").exists()


def test_failed_conversion_keeps_the_previous_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "llmon"
    raw.mkdir(parents=True)
    _zip_with_bad_second_member(raw / "llmon_202607.zip")
    previous = tmp_path / "bronze" / "llmon" / "llmon_202607.parquet"
    previous.parent.mkdir(parents=True)
    pl.DataFrame({"text_content": ["kept"]}).write_parquet(previous)

    result = import_prefix_bronze(
        "llmon", tmp_path / "raw", config=_config(tmp_path, overwrite=True)
    )

    assert result.failed == 1
    assert pl.read_parquet(previous)["text_content"].to_list() == ["kept"]


def test_pipeline_status_reports_bronze_failures(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "llmon"
    raw.mkdir(parents=True)
    _zip_with_bad_second_member(raw / "llmon_202607.zip")

    out = run_processing(["llmon"], include_transformation=False, config=_config(tmp_path))

    assert out["staging_summary"]["total_failed"] == 1
    assert out["summary"]["status"] == "completed_with_failures"
    assert out["staging_results"]["llmon"]["errors"]


def test_silver_failure_is_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bronze = tmp_path / "bronze" / "llmon"
    bronze.mkdir(parents=True)
    pl.DataFrame({"text_content": ["HH|202607|MO|20260807", "TT|202607|0"]}).write_parquet(
        bronze / "llmon_202607.parquet"
    )

    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(import_silver, "transform_delimited_canonical", boom)
    result = import_prefix_silver_canonical(
        "llmon",
        tmp_path / "bronze",
        tmp_path / "silver",
        SCHEMAS / "canonical",
        _config(tmp_path),
        record_types=["HH"],
    )

    assert (result.successful, result.failed) == (0, 1)
    assert "injected" in result.errors[0]["error"]
    assert not (tmp_path / "silver" / "llmon" / "HH").exists()


@pytest.mark.parametrize(
    ("command", "summary_key"),
    [("bronze", "staging_summary"), ("silver", "transformation_summary")],
)
def test_cli_exits_1_on_failure(
    monkeypatch: pytest.MonkeyPatch, command: str, summary_key: str
) -> None:
    fake = {
        "staging_summary": {"total_successful": 2, "total_failed": 1, "total_skipped": 0},
        "transformation_summary": {
            "total_files": 3,
            "total_successful": 2,
            "total_failed": 1,
            "total_records": 10,
        },
        "staging_results": {"llmon": {"errors": [{"file": "f", "error": "e"}]}},
        "transformation_results": {"llmon": {"errors": [{"file": "f", "error": "e"}]}},
    }
    monkeypatch.setattr(gnma_cli, "core_processing_workflow", lambda **kw: fake)
    result = CliRunner().invoke(gnma_cli.app, [command, "llmon"])
    assert result.exit_code == 1

    fake[summary_key]["total_failed"] = 0
    result = CliRunner().invoke(gnma_cli.app, [command, "llmon"])
    assert result.exit_code == 0
