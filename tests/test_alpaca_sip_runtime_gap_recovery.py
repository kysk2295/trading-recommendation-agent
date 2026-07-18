from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from tests.test_alpaca_sip_runtime_adapter import (
    _SOURCE_ID,
    _SYMBOL,
    _adapter,
    _decision,
    _feature_requests,
    _wire_bar,
)
from trading_agent.us_market_data_runtime_models import MarketDataRuntimeStatus
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore
from trading_agent.us_market_data_supervisor import UsMarketDataSupervisor

_NY = ZoneInfo("America/New_York")


def test_incomplete_backfill_preserves_visible_gap_block(tmp_path: Path) -> None:
    now = dt.datetime(2026, 7, 17, 9, 33, 30, tzinfo=_NY)

    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "bars": {_SYMBOL: (_wire_bar(0), _wire_bar(2))},
                "next_page_token": None,
            },
        )

    adapter, evidence = _adapter(tmp_path, now, respond)
    runtime = MarketDataRuntimeStore(tmp_path / "runtime.sqlite3")
    supervisor = UsMarketDataSupervisor(adapter, runtime, clock=lambda: now)

    first = supervisor.run_cycle(_decision(now), _feature_requests())
    still_blocked = supervisor.run_cycle(_decision(now), _feature_requests())

    assert first.status is MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP
    assert still_blocked.status is MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP
    assert still_blocked.inserted_receipt_count == 0
    assert still_blocked.last_sequence == 3
    assert runtime.receipt_count(_SOURCE_ID) == 2
    assert evidence.page_count() == 1
    assert evidence.projection_count() == 1
