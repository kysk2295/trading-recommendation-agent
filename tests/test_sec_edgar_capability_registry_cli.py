from __future__ import annotations

import datetime as dt
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import typer

import run_sec_edgar_capability_registry as cli
from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_history_collection import collect_sec_additional_history
from trading_agent.sec_edgar_models import SecSubmissionRawResponse
from trading_agent.sec_edgar_store import SecEdgarStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_sec_edgar_capability_registry.py"
RECENT = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
HISTORY = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
CIK = "0000320193"
COLLECTION_ID = "sec-capability-cli-001"
RECENT_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
HISTORY_AT = RECENT_AT + dt.timedelta(minutes=1)
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)


class _RecentFetcher:
    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=RECENT_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=RECENT.read_bytes(),
        )


class _HistoryFetcher:
    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        _ = file_name
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=HISTORY_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=HISTORY.read_bytes(),
        )


def test_help_exposes_query_only_projection_without_provider_controls() -> None:
    completed = subprocess.run(
        (str(UV), "run", "--script", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--parent-collection-id" in completed.stdout
    assert "--registry" in completed.stdout
    assert "--arm" not in completed.stdout


def test_complete_evidence_appends_once_and_replays_redacted_registry(tmp_path: Path) -> None:
    database = _database(tmp_path, include_history=True)
    registry = tmp_path / "registry" / "capabilities.sqlite3"
    output = tmp_path / "report"

    _run(database, registry, output)
    first = (output / cli.REPORT_NAME).read_text(encoding="utf-8")
    _run(database, registry, output)
    report_path = output / cli.REPORT_NAME
    second = report_path.read_text(encoding="utf-8")

    assert "result: complete" in first
    assert "successful slices: 2/2" in first
    assert "capability appended: 1" in first
    assert "entitlement appended: 1" in first
    assert "capability appended: 0" in second
    assert "entitlement appended: 0" in second
    assert "capability resolved: 1/1" in second
    assert CIK not in first + second
    assert COLLECTION_ID not in first + second
    assert str(tmp_path) not in first + second
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_missing_history_is_persisted_as_incomplete_with_exit_two(tmp_path: Path) -> None:
    database = _database(tmp_path, include_history=False)
    output = tmp_path / "report"

    with pytest.raises(typer.Exit) as raised:
        _run(database, tmp_path / "registry.sqlite3", output)

    report = (output / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert raised.value.exit_code == 2
    assert "result: incomplete" in report
    assert "successful slices: 1/2" in report
    assert "missing slices: 1" in report


def test_path_collision_and_invalid_cik_block_before_registry_write(tmp_path: Path) -> None:
    database = _database(tmp_path, include_history=True)
    report = tmp_path / "report"

    with pytest.raises(typer.BadParameter):
        _run(database, database, report)
    with pytest.raises(typer.BadParameter):
        cli.main(
            parent_collection_id=COLLECTION_ID,
            cik="320193",
            database=str(database),
            registry=str(tmp_path / "registry.sqlite3"),
            output_dir=str(report),
        )


def test_cli_source_does_not_import_provider_credentials_or_execution() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "sec_edgar_client" not in source
    assert "sec_edgar_config" not in source
    assert "alpaca" not in source.lower()
    assert "order" not in source.lower()


def _database(tmp_path: Path, *, include_history: bool) -> Path:
    database = tmp_path / "ledger" / "sec.sqlite3"
    store = SecEdgarStore(database)
    _ = collect_sec_submissions(
        _RecentFetcher(),
        store,
        COLLECTION_ID,
        CIK,
        _clock=lambda: RECENT_AT + dt.timedelta(seconds=5),
    )
    if include_history:
        _ = collect_sec_additional_history(
            _HistoryFetcher(),
            store,
            COLLECTION_ID,
            CIK,
            _clock=lambda: HISTORY_AT + dt.timedelta(seconds=5),
        )
    return database


def _run(database: Path, registry: Path, output: Path) -> None:
    cli.main(
        parent_collection_id=COLLECTION_ID,
        cik=CIK,
        database=str(database),
        registry=str(registry),
        output_dir=str(output),
    )
