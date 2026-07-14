from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisCredentials, KisSession
from tests.test_kis_scan_cli import _engine
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import KisPaperScanner
from trading_agent.store import PaperStore


def test_observe_stock_archives_the_exact_signal_input_context(
    tmp_path: Path,
) -> None:
    minute_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": "093200",
                "kymd": "20260710",
                "khms": "223200",
                "open": "10.50",
                "high": "10.80",
                "low": "10.40",
                "last": "10.70",
                "evol": "50000",
                "eamt": "535000",
            }
        ],
    }
    daily_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {"xymd": "20260709", "clos": "10.00", "tvol": "150000"},
            {"xymd": "20260708", "clos": "9.50", "tvol": "250000"},
        ],
    }
    stock = KisRankedStock(
        "NAS",
        "INPUT",
        "Input Corp",
        10.70,
        0.07,
        10.69,
        10.71,
        150_000,
        1_605_000.0,
        100_000,
        1,
    )
    store = PaperStore(tmp_path / "paper.sqlite3")
    observed_at = dt.datetime(
        2026,
        7,
        10,
        9,
        33,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = daily_payload if request.url.path.endswith("/dailyprice") else minute_payload
        return httpx2.Response(200, json=payload)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).observe(stock, 1, observed_at)

    with sqlite3.connect(store.path) as connection:
        archived: tuple[str, str, str, str, float, int, float] | None = connection.execute(
            "SELECT exchange, symbol, observed_at, latest_completed_bar_at, "
            "prior_close, average_daily_volume, spread_bps "
            "FROM candidate_input_snapshots"
        ).fetchone()
    assert observation.status == "최신 완료 봉 평가"
    assert archived == (
        "NAS",
        "INPUT",
        observed_at.isoformat(),
        dt.datetime(2026, 7, 10, 9, 32, tzinfo=ZoneInfo("America/New_York")).isoformat(),
        10.0,
        200_000,
        stock.spread_bps,
    )
