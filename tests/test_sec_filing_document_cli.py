from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest
import typer

import run_sec_filing_document_collect
from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_models import SecSubmissionRawResponse
from trading_agent.sec_edgar_store import SecEdgarStore
from trading_agent.sec_filing_document_target import read_sec_filing_document_targets

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
CIK = "0000320193"
COLLECTION_ID = "sec-document-cli-001"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)


class _Fetcher:
    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=RECEIVED_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=FIXTURE.read_bytes(),
        )


def test_cli_fixture_happy_and_provider_free_replay_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = tmp_path / "metadata" / "sec.sqlite3"
    _ = collect_sec_submissions(
        _Fetcher(),
        SecEdgarStore(metadata),
        COLLECTION_ID,
        CIK,
        _clock=lambda: RECEIVED_AT + dt.timedelta(seconds=1),
    )
    target = read_sec_filing_document_targets(
        metadata,
        COLLECTION_ID,
        CIK,
        limit=1,
    )[0]
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "document.htm").write_bytes(b"<html>fixture filing</html>")
    manifest = fixture / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "responses": [
                    {
                        "target_id": target.target_id,
                        "received_at": "2026-07-20T14:01:00+00:00",
                        "http_status": 200,
                        "content_type": "text/html",
                        "payload_path": "document.htm",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    documents = tmp_path / "documents" / "sec.sqlite3"
    output = tmp_path / "report"
    moments = iter(
        (
            RECEIVED_AT + dt.timedelta(seconds=59),
            RECEIVED_AT + dt.timedelta(seconds=61),
        )
    )
    monkeypatch.setattr(run_sec_filing_document_collect, "_utc_now", lambda: next(moments))

    run_sec_filing_document_collect.main(
        COLLECTION_ID,
        CIK,
        str(metadata),
        str(documents),
        str(output),
        1,
        str(manifest),
        None,
    )
    run_sec_filing_document_collect.main(
        COLLECTION_ID,
        CIK,
        str(metadata),
        str(documents),
        str(output),
        1,
        None,
        str(tmp_path / "missing.env"),
    )

    report_path = output / "sec_filing_document_summary.md"
    report = report_path.read_text(encoding="utf-8")
    assert "documents completed: 1" in report
    assert "documents replayed: 1" in report
    assert "raw bytes retained: 27" in report
    assert CIK not in report
    assert target.accession_number not in report
    assert target.primary_document not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_cli_rejects_document_database_report_alias_before_collection(tmp_path: Path) -> None:
    report_database = tmp_path / "output" / "sec_filing_document_summary.md"

    with pytest.raises(
        typer.BadParameter,
        match="metadata, document, and report paths must be distinct",
    ):
        run_sec_filing_document_collect.main(
            COLLECTION_ID,
            CIK,
            str(tmp_path / "metadata.sqlite3"),
            str(report_database),
            str(report_database.parent),
            1,
            None,
            None,
        )

    assert report_database.exists() is False
