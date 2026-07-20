from __future__ import annotations

import datetime as dt
import gzip
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
    assert snapshot.additional_history_file_count == 1
    assert tuple(item.form for item in snapshot.filings) == ("8-K", "10-Q")
    assert snapshot.filings[0].items == ("2.02", "9.01")
    assert snapshot.filings[1].report_date is None
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


def test_sec_parser_canonicalizes_equivalent_acceptance_offset_to_utc() -> None:
    document = json.loads(FIXTURE.read_bytes())
    document["filings"]["recent"]["acceptanceDateTime"][0] = "2026-07-20T09:30:00-04:00"

    snapshot = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))

    assert snapshot.filings[0].accepted_at == dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.UTC)
    assert snapshot.filings[0].accepted_at.isoformat() == "2026-07-20T13:30:00+00:00"


def test_sec_parser_bounds_decoded_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_agent.sec_edgar_parser as parser_module

    monkeypatch.setattr(parser_module, "_MAX_DECODED_BYTES", 512)
    response = SecSubmissionRawResponse(
        collection_id="sec-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=gzip.compress(b"x" * 1_024),
        content_encoding="gzip",
    )

    with pytest.raises(SecEdgarResponseError, match="decoded_response_too_large"):
        _ = parse_sec_submission_snapshot(response)


def test_sec_parser_rejects_naive_acceptance_time() -> None:
    document = json.loads(FIXTURE.read_bytes())
    document["filings"]["recent"]["acceptanceDateTime"][0] = "2026-07-20T13:30:00"

    with pytest.raises(SecEdgarResponseError, match="acceptance_time"):
        _ = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))


def test_sec_parser_rejects_raw_accession_from_another_cik() -> None:
    document = json.loads(FIXTURE.read_bytes())
    accession = document["filings"]["recent"]["accessionNumber"][0]
    document["filings"]["recent"]["accessionNumber"][0] = f"0000000001{accession[10:]}"

    with pytest.raises(SecEdgarResponseError) as captured:
        _ = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))

    assert captured.value.failure_code == "accession_cik_mismatch"


def test_sec_parser_ignores_unconsumed_issuer_and_history_metadata() -> None:
    document = json.loads(FIXTURE.read_bytes())
    document["name"] = {"not": "consumed"}
    document["tickers"] = "not-consumed"
    document["exchanges"] = None
    document["filings"]["files"] = [{"unrecognized": [1, 2, 3]}]

    snapshot = parse_sec_submission_snapshot(_response(json.dumps(document).encode()))

    assert snapshot.additional_history_file_count == 1
    assert len(snapshot.filings) == 2


def _response(payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id="sec-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )
