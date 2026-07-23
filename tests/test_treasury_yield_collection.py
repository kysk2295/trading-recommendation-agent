from __future__ import annotations

import datetime as dt

from tests.test_treasury_yield_parser import FIXTURE, RECEIVED, _request
from trading_agent.treasury_yield_collection import collect_treasury_yield
from trading_agent.treasury_yield_models import (
    TreasuryYieldFailure,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
    TreasuryYieldStatus,
)
from trading_agent.treasury_yield_store import TreasuryYieldStore

STARTED = RECEIVED - dt.timedelta(seconds=1)
COMPLETED = RECEIVED + dt.timedelta(seconds=1)


class _Fetcher:
    __slots__ = ("_payload", "calls")

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.calls = 0

    def fetch(
        self,
        request: TreasuryYieldRequest,
    ) -> TreasuryYieldRawResponse:
        self.calls += 1
        return TreasuryYieldRawResponse(
            request_id=request.request_id,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/xml",
            raw_payload=self._payload,
        )


def test_malformed_xml_is_preserved_before_failed_terminal(
    tmp_path,
) -> None:
    # Given
    store = TreasuryYieldStore(tmp_path / "treasury-yield.sqlite3")
    store.preflight_write()
    request = _request()

    # When
    result = collect_treasury_yield(
        _Fetcher(b"<feed"),
        store,
        request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )

    # Then
    assert result.run.status is TreasuryYieldStatus.FAILED
    assert result.run.failure is TreasuryYieldFailure.RESPONSE_STRUCTURE
    assert store.counts() == (1, 1)
    receipt = store.receipt(request.request_id)
    assert receipt is not None
    assert receipt.raw_payload == b"<feed"


def test_successful_terminal_replays_without_fetching(tmp_path) -> None:
    # Given
    store = TreasuryYieldStore(tmp_path / "treasury-yield.sqlite3")
    store.preflight_write()
    request = _request()
    first_fetcher = _Fetcher(FIXTURE.read_bytes())
    first = collect_treasury_yield(
        first_fetcher,
        store,
        request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )
    replay_fetcher = _Fetcher(b"not-xml")

    # When
    replay = collect_treasury_yield(
        replay_fetcher,
        store,
        request,
    )

    # Then
    assert first.run.status is TreasuryYieldStatus.SUCCESS
    assert first.replayed is False
    assert replay.run == first.run
    assert replay.replayed is True
    assert first_fetcher.calls == 1
    assert replay_fetcher.calls == 0
    assert store.counts() == (1, 1)
