from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_cftc_tff_parser import FIXTURE, RECEIVED
from trading_agent.cftc_tff_collection import collect_cftc_tff
from trading_agent.cftc_tff_models import (
    CftcTffFailure,
    CftcTffRawResponse,
    CftcTffRequest,
    CftcTffStatus,
)
from trading_agent.cftc_tff_store import CftcTffStore

STARTED = RECEIVED - dt.timedelta(seconds=1)
COMPLETED = RECEIVED + dt.timedelta(seconds=1)


class _Fetcher:
    __slots__ = ("_payload", "calls")

    def __init__(self, payload: bytes) -> None:
        self.calls = 0
        self._payload = payload

    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse:
        self.calls += 1
        return CftcTffRawResponse(
            request_id=request.request_id,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/json",
            raw_payload=self._payload,
        )


def test_malformed_response_is_preserved_before_failed_terminal(
    tmp_path: Path,
) -> None:
    # Given
    store = CftcTffStore(tmp_path / "cftc-tff.sqlite3")
    store.preflight_write()
    request = _request()

    # When
    result = collect_cftc_tff(
        _Fetcher(b"{"),
        store,
        request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )

    # Then
    assert result.run.status is CftcTffStatus.FAILED
    assert result.run.failure is CftcTffFailure.RESPONSE_STRUCTURE
    assert store.counts() == (1, 1)
    receipt = store.receipt(request.request_id)
    assert receipt is not None
    assert receipt.raw_payload == b"{"


def test_successful_terminal_replays_without_fetching(
    tmp_path: Path,
) -> None:
    # Given
    store = CftcTffStore(tmp_path / "cftc-tff.sqlite3")
    store.preflight_write()
    request = _request()
    first_fetcher = _Fetcher(FIXTURE.read_bytes())
    first = collect_cftc_tff(
        first_fetcher,
        store,
        request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )
    replay_fetcher = _Fetcher(b"not-json")

    # When
    replay = collect_cftc_tff(
        replay_fetcher,
        store,
        request,
    )

    # Then
    assert first.run.status is CftcTffStatus.SUCCESS
    assert first.replayed is False
    assert replay.run == first.run
    assert replay.replayed is True
    assert first_fetcher.calls == 1
    assert replay_fetcher.calls == 0
    assert store.counts() == (1, 1)


def _request() -> CftcTffRequest:
    return CftcTffRequest(
        collection_id="es-tff-20260724",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )
