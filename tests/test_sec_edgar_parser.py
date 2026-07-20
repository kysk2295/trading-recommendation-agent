from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.sec_edgar_models import (
    SecEdgarResponseError,
    SecSubmissionRawResponse,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)


def test_sec_parser_projects_columnar_recent_filings() -> None:
    # Given
    response = _response(FIXTURE.read_bytes())

    # When
    snapshot = parse_sec_submission_snapshot(response)

    # Then
    assert snapshot.cik == "0000320193"
    assert snapshot.tickers == ("EXM",)
    assert snapshot.additional_history_file_count == 1
    assert tuple(item.form for item in snapshot.filings) == ("8-K", "10-Q")
    assert snapshot.filings[0].items == ("2.02", "9.01")
    assert snapshot.filings[1].report_date is None
    assert len(snapshot.filings[0].event_id) == 64
    assert b"0000320193-26-000101" not in repr(response).encode()


def test_sec_parser_rejects_column_length_mismatch() -> None:
    # Given
    document = json.loads(FIXTURE.read_bytes())
    document["filings"]["recent"]["form"].pop()

    # When / Then
    with pytest.raises(SecEdgarResponseError, match="column_lengths"):
        _ = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))


def test_sec_parser_rejects_future_acceptance_time() -> None:
    # Given
    document = json.loads(FIXTURE.read_bytes())
    document["filings"]["recent"]["acceptanceDateTime"][0] = "2026-07-20T15:00:00Z"

    # When / Then
    with pytest.raises(SecEdgarResponseError, match="acceptance_time"):
        _ = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))


def _response(payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id="sec-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )
