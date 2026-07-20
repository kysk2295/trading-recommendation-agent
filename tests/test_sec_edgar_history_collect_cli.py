from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest
import typer

import run_sec_edgar_history_collect
from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_models import SecSubmissionRawResponse
from trading_agent.sec_edgar_store import SecEdgarStore

RECENT = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
HISTORY = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
PRIMARY_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
PRIMARY_COMPLETED_AT = PRIMARY_AT + dt.timedelta(minutes=10)


class RecentFetcher:
    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=PRIMARY_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=RECENT.read_bytes(),
        )


def test_sec_history_cli_fixture_happy_and_replay_are_redacted(tmp_path: Path) -> None:
    database = tmp_path / "ledger" / "sec.sqlite3"
    store = SecEdgarStore(database)
    _ = collect_sec_submissions(
        RecentFetcher(),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: PRIMARY_COMPLETED_AT,
    )
    output = tmp_path / "report"

    run_sec_edgar_history_collect.main(
        parent_collection_id="sec-cycle-001",
        cik="0000320193",
        database=str(database),
        output_dir=str(output),
        max_files=1,
        fixture_manifest=str(_manifest(tmp_path / "fixture")),
        user_agent_path=None,
    )
    first = _report(output)
    run_sec_edgar_history_collect.main(
        parent_collection_id="sec-cycle-001",
        cik="0000320193",
        database=str(database),
        output_dir=str(output),
        max_files=1,
        fixture_manifest=None,
        user_agent_path=str(tmp_path / "missing.env"),
    )
    second = _report(output)

    assert "history files discovered: 1" in first
    assert "history files completed: 1" in first
    assert "new filing versions: 1" in first
    assert "history files replayed: 0" in first
    assert "history files replayed: 1" in second
    assert "0000320193" not in first + second
    assert "CIK0000320193" not in first + second
    assert stat.S_IMODE((output / "sec_edgar_history_summary.md").stat().st_mode) == 0o600


def test_sec_history_cli_rejects_missing_parent_before_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_opened = False

    def reject_source(_path: Path) -> None:
        nonlocal source_opened
        source_opened = True

    monkeypatch.setattr(
        run_sec_edgar_history_collect,
        "load_sec_edgar_history_fixture",
        reject_source,
    )

    with pytest.raises(typer.BadParameter):
        run_sec_edgar_history_collect.main(
            parent_collection_id="sec-cycle-missing",
            cik="0000320193",
            database=str(tmp_path / "sec.sqlite3"),
            output_dir=str(tmp_path / "report"),
            max_files=1,
            fixture_manifest=str(tmp_path / "fixture.json"),
            user_agent_path=None,
        )

    assert source_opened is False


def test_sec_history_cli_redacts_report_preflight_path_error(tmp_path: Path) -> None:
    database = _database_with_parent(tmp_path, "sec-cycle-report-error")
    private_component = tmp_path / "private-report-component"
    private_component.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        typer.BadParameter,
        match=r"^SEC EDGAR history collection state is invalid$",
    ):
        run_sec_edgar_history_collect.main(
            parent_collection_id="sec-cycle-report-error",
            cik="0000320193",
            database=str(database),
            output_dir=str(private_component / "report"),
            max_files=1,
            fixture_manifest=None,
            user_agent_path=None,
        )


def test_sec_history_cli_redacts_user_agent_path_error(tmp_path: Path) -> None:
    database = _database_with_parent(tmp_path, "sec-cycle-user-agent-error")
    private_user_agent = tmp_path / "private-user-agent.env"

    with pytest.raises(
        typer.BadParameter,
        match=r"^SEC EDGAR history collection state is invalid$",
    ):
        run_sec_edgar_history_collect.main(
            parent_collection_id="sec-cycle-user-agent-error",
            cik="0000320193",
            database=str(database),
            output_dir=str(tmp_path / "report"),
            max_files=1,
            fixture_manifest=None,
            user_agent_path=str(private_user_agent),
        )


def _manifest(directory: Path) -> Path:
    directory.mkdir()
    (directory / "history.json").write_bytes(HISTORY.read_bytes())
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "responses": [
                    {
                        "file_name": "CIK0000320193-submissions-001.json",
                        "received_at": "2026-07-20T14:01:00+00:00",
                        "http_status": 200,
                        "content_type": "application/json",
                        "payload_path": "history.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _database_with_parent(tmp_path: Path, collection_id: str) -> Path:
    database = tmp_path / f"{collection_id}.sqlite3"
    _ = collect_sec_submissions(
        RecentFetcher(),
        SecEdgarStore(database),
        collection_id,
        "0000320193",
        _clock=lambda: PRIMARY_COMPLETED_AT,
    )
    return database


def _report(output: Path) -> str:
    return (output / "sec_edgar_history_summary.md").read_text(encoding="utf-8")
