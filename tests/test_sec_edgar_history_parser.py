from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.sec_edgar_history_parser import parse_sec_additional_history_snapshot
from trading_agent.sec_edgar_models import (
    SecAdditionalHistoryFile,
    SecEdgarResponseError,
    SecSubmissionRawResponse,
)

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, 1, tzinfo=dt.UTC)
MANIFEST = SecAdditionalHistoryFile(
    cik="0000320193",
    name="CIK0000320193-submissions-001.json",
    filing_count=1,
    filing_from=dt.date(1994, 1, 1),
    filing_to=dt.date(2025, 12, 31),
)


def test_sec_history_parser_projects_manifest_bound_filing() -> None:
    response = _response(FIXTURE.read_bytes())

    snapshot = parse_sec_additional_history_snapshot(response, MANIFEST)

    assert snapshot.cik == MANIFEST.cik
    assert snapshot.additional_history_files == ()
    assert tuple(item.accession_number for item in snapshot.filings) == (
        "0000320193-25-000001",
    )


def test_sec_history_parser_rejects_manifest_count_mismatch() -> None:
    manifest = MANIFEST.model_copy(update={"filing_count": 2})

    with pytest.raises(SecEdgarResponseError, match="history_manifest"):
        _ = parse_sec_additional_history_snapshot(_response(FIXTURE.read_bytes()), manifest)


def test_sec_history_parser_rejects_filing_outside_manifest_range() -> None:
    document = json.loads(FIXTURE.read_bytes())
    document["filingDate"][0] = "2026-01-01"

    with pytest.raises(SecEdgarResponseError, match="history_manifest"):
        _ = parse_sec_additional_history_snapshot(
            _response(json.dumps(document).encode()),
            MANIFEST,
        )


def test_sec_history_parser_rejects_root_column_item_2001_before_model_load() -> None:
    document = json.loads(FIXTURE.read_bytes())
    document["accessionNumber"] = [document["accessionNumber"][0] for _ in range(2_001)]

    with pytest.raises(SecEdgarResponseError, match="response_structure"):
        _ = parse_sec_additional_history_snapshot(
            _response(json.dumps(document).encode()),
            MANIFEST,
        )


def _response(payload: bytes) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id="sec-history-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )
