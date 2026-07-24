from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.bls_public_collection import collect_bls_public_data
from trading_agent.bls_public_models import (
    BlsPublicRawResponse,
    BlsPublicRequest,
    BlsPublicStatus,
)
from trading_agent.bls_public_store import BlsPublicStore

FIXTURE = Path(__file__).parent / "fixtures/bls_public_data/macro_two_series.json"
RECEIVED = dt.datetime(2026, 7, 24, 1, 2, 3, tzinfo=dt.UTC)
COMPLETED = dt.datetime(2026, 7, 24, 1, 2, 4, tzinfo=dt.UTC)


class _NoNetworkFetcher:
    def fetch(self, request: BlsPublicRequest) -> BlsPublicRawResponse:
        raise AssertionError(f"unexpected network fetch for {request.request_id}")


def test_collection_recovers_orphan_raw_receipt_without_refetch(
    tmp_path: Path,
) -> None:
    request = BlsPublicRequest(
        collection_id="bls-macro-20260724",
        series_ids=("CUUR0000SA0", "LNS14000000"),
        start_year=2025,
        end_year=2026,
    )
    response = BlsPublicRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/json",
        raw_payload=FIXTURE.read_bytes(),
    )
    store = BlsPublicStore(tmp_path / "state/bls.sqlite3")
    assert store.append_receipt(request, response)

    result = collect_bls_public_data(
        _NoNetworkFetcher(),
        store,
        request,
        _clock=lambda: COMPLETED,
    )

    assert result.run.status is BlsPublicStatus.SUCCESS
    assert not result.fetched
    assert not result.replayed
    assert result.run.receipt_id == response.receipt_id
    assert store.counts() == (1, 1)
