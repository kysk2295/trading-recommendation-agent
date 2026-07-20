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


def _report(output: Path) -> str:
    return (output / "sec_edgar_history_summary.md").read_text(encoding="utf-8")
