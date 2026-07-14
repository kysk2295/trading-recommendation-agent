from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisCredentials, KisSession
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import KisPaperScanner
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig


def test_follow_archives_bars_without_creating_a_new_signal(tmp_path: Path) -> None:
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
    stock = KisRankedStock(
        "NAS",
        "FOLLOW",
        "Follow Corp",
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
    observed_at = dt.datetime(
        2026,
        7,
        10,
        9,
        33,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=minute_payload)
        ),
    ) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            engine,
        ).follow(stock, 1, observed_at)

    with sqlite3.connect(store.path) as connection:
        archived = connection.execute(
            "SELECT COUNT(*) FROM candidate_minute_bars WHERE symbol = ?",
            ("FOLLOW",),
        ).fetchone()
    assert observation.status == "추적 분봉 보존"
    assert archived == (3,)
    assert store.recommendations() == ()


def test_follow_advances_an_existing_recommendation_without_a_new_one(
    tmp_path: Path,
) -> None:
    minute_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": "093500",
                "kymd": "20260710",
                "khms": "223500",
                "open": "10.50",
                "high": "11.60",
                "low": "10.40",
                "last": "11.50",
                "evol": "50000",
                "eamt": "575000",
            }
        ],
    }
    daily_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {"xymd": "20260709", "clos": "10.00", "tvol": "100000"}
        ],
    }
    stock = KisRankedStock(
        "NAS",
        "FOLLOW",
        "Follow Corp",
        11.50,
        0.15,
        11.49,
        11.51,
        150_000,
        1_725_000.0,
        100_000,
        1,
    )
    store = PaperStore(tmp_path / "paper.sqlite3")
    created_at = dt.datetime(
        2026,
        7,
        10,
        9,
        34,
        tzinfo=ZoneInfo("America/New_York"),
    )
    store.save(
        Recommendation(
            "follow-existing",
            "FOLLOW",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.ACTIVE,
            "follow fixture",
        )
    )
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig()),
        OpeningRangeBreakout(OrbConfig()),
        RiskConfig(),
        store,
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = (
            daily_payload
            if request.url.path.endswith("/dailyprice")
            else minute_payload
        )
        return httpx2.Response(200, json=payload)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            engine,
        ).follow(
            stock,
            1,
            dt.datetime(
                2026,
                7,
                10,
                9,
                36,
                30,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        )

    assert observation.status == "추적 추천 상태 갱신"
    assert len(store.recommendations()) == 1
    assert store.recommendations()[0].state is RecommendationState.TARGET_2R
