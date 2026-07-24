from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.bls_public_models import (
    BlsPublicError,
    BlsPublicRawResponse,
    BlsPublicRequest,
)
from trading_agent.bls_public_parser import parse_bls_macro_snapshot

FIXTURE = Path(__file__).parent / "fixtures/bls_public_data/macro_two_series.json"


def test_parser_preserves_footnoted_provider_missing_observation() -> None:
    request = BlsPublicRequest(
        collection_id="bls-macro-20260724",
        series_ids=("CUUR0000SA0", "LNS14000000"),
        start_year=2025,
        end_year=2026,
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["Results"]["series"][0]["data"][0]["value"] = "-"
    payload["Results"]["series"][0]["data"][0]["footnotes"] = [
        {
            "code": "X",
            "text": "Data unavailable due to the 2025 lapse in appropriations.",
        }
    ]
    response = BlsPublicRawResponse(
        request_id=request.request_id,
        received_at=dt.datetime(2026, 7, 24, 1, 2, 3, tzinfo=dt.UTC),
        status_code=200,
        content_type="application/json",
        raw_payload=json.dumps(payload).encode(),
    )

    snapshot = parse_bls_macro_snapshot(request, response)

    observation = snapshot.series[0].observations[0]
    assert observation.value is None
    assert observation.footnotes[0].code == "X"
    assert snapshot.available_observation_count == 3
    assert snapshot.missing_observation_count == 1
    assert snapshot.observed_completeness_bps == 7_500


def test_parser_rejects_unfootnoted_provider_placeholder() -> None:
    request = BlsPublicRequest(
        collection_id="bls-macro-20260724",
        series_ids=("CUUR0000SA0", "LNS14000000"),
        start_year=2025,
        end_year=2026,
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["Results"]["series"][0]["data"][0]["value"] = "-"
    response = BlsPublicRawResponse(
        request_id=request.request_id,
        received_at=dt.datetime(2026, 7, 24, 1, 2, 3, tzinfo=dt.UTC),
        status_code=200,
        content_type="application/json",
        raw_payload=json.dumps(payload).encode(),
    )

    with pytest.raises(BlsPublicError):
        _ = parse_bls_macro_snapshot(request, response)
