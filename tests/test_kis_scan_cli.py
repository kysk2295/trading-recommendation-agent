from __future__ import annotations

import datetime as dt
import math
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisCredentials, KisSession
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import KisPaperScanner
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig


def test_observe_stock_rejects_stale_session_with_finite_spread(
    tmp_path: Path,
) -> None:
    stale_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": f"093{minute}00",
                "kymd": "20260710",
                "khms": f"223{minute}00",
                "open": "10.50",
                "high": "10.80",
                "low": "10.40",
                "last": "10.70",
                "evol": "50000",
                "eamt": "535000",
            }
            for minute in range(3)
        ],
    }
    stock = KisRankedStock(
        "NAS",
        "STALE",
        "Stale Quote",
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
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=stale_payload)),
    ) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            engine,
        ).observe(
            stock,
            2,
            dt.datetime(
                2026,
                7,
                12,
                10,
                0,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        )

    assert math.isfinite(observation.spread_bps)
    assert observation.status == "시장 폐장 또는 분봉 지연"
    assert store.recommendations() == ()


def test_observe_stock_persists_and_reuses_the_latest_bar_checkpoint(
    tmp_path: Path,
) -> None:
    minute_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": f"093{minute}00",
                "kymd": "20260710",
                "khms": f"223{minute}00",
                "open": "10.50",
                "high": "10.80" if minute == 2 else "10.60",
                "low": "10.40",
                "last": "10.70" if minute == 2 else "10.50",
                "evol": "50000",
                "eamt": "535000",
            }
            for minute in range(3)
        ],
    }
    daily_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {"xymd": "20260709", "clos": "10.00", "tvol": "100000"},
            {"xymd": "20260708", "clos": "9.90", "tvol": "100000"},
        ],
    }
    stock = KisRankedStock(
        "NAS",
        "LIVE",
        "Live Quote",
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
    repeated_at = observed_at + dt.timedelta(minutes=1)

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = daily_payload if request.url.path.endswith("/dailyprice") else minute_payload
        return httpx2.Response(200, json=payload)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        first = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).observe(stock, 2, observed_at)
        second = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).observe(stock, 2, repeated_at)

    checkpoint = dt.datetime(2026, 7, 10, 9, 32, tzinfo=ZoneInfo("America/New_York"))
    assert first.status == "최신 완료 봉 평가"
    assert second.status == "이미 처리한 봉"
    assert store.last_processed_bar("LIVE") == checkpoint
    assert len(store.recommendations()) == 1
    with sqlite3.connect(store.path) as connection:
        archived: tuple[int, str, str] | None = connection.execute(
            "SELECT COUNT(*), MIN(first_observed_at), MAX(first_observed_at) "
            "FROM candidate_minute_bars WHERE symbol = ?",
            ("LIVE",),
        ).fetchone()
    assert archived == (3, observed_at.isoformat(), observed_at.isoformat())


def test_observe_stock_formats_http_failure_on_one_report_line(
    tmp_path: Path,
) -> None:
    stock = KisRankedStock(
        "NAS",
        "ERROR",
        "Broken Feed",
        10.0,
        0.1,
        9.99,
        10.01,
        100_000,
        1_000_000.0,
        100_000,
        1,
    )
    store = PaperStore(tmp_path / "paper.sqlite3")

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(500, request=request)),
    ) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).observe(
            stock,
            1,
            dt.datetime(
                2026,
                7,
                10,
                9,
                33,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        )

    assert observation.status.startswith("오류:")
    assert "\n" not in observation.status


def _engine(store: PaperStore) -> RecommendationEngine:
    return RecommendationEngine(
        MomentumScanner(ScannerConfig(min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
