from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisCredentials, KisMinutePayload, KisSession
from trading_agent.bar_archive import track_candidates, tracked_candidates_for_session
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_eod import catch_up_candidates
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import KisPaperScanner
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig

NEW_YORK = ZoneInfo("America/New_York")


def test_eod_catchup_archives_the_1559_bar_and_advances_only_existing_recommendations(tmp_path: Path) -> None:
    session_date = dt.date(2026, 7, 10)
    stock = _stock()
    store = _active_store(tmp_path / "paper.sqlite3", session_date)

    with _client(_minute_payload("155900")) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).catch_up_after_close(
            stock,
            max_pages=1,
            session_date=session_date,
            now=dt.datetime(2026, 7, 10, 16, 1, 5, tzinfo=NEW_YORK),
        )

    assert observation.status == "장마감 마지막 봉 보존·열린 추천 갱신"
    assert observation.bars == 1
    assert store.last_processed_bar("EOD") == dt.datetime(2026, 7, 10, 15, 59, tzinfo=NEW_YORK)
    assert len(store.recommendations()) == 1
    with sqlite3.connect(store.path) as connection:
        row: tuple[str] | None = connection.execute(
            "SELECT exchange_timestamp FROM candidate_minute_bars WHERE symbol='EOD'"
        ).fetchone()
    assert row == ("2026-07-10T15:59:00-04:00",)


def test_eod_catchup_fails_closed_when_the_last_regular_bar_is_missing(tmp_path: Path) -> None:
    session_date = dt.date(2026, 7, 10)
    store = _active_store(tmp_path / "paper.sqlite3", session_date)

    with _client(_minute_payload("155800")) as client:
        observation = KisPaperScanner(
            client,
            KisSession(KisCredentials("key", "secret"), "token"),
            _engine(store),
        ).catch_up_after_close(
            _stock(),
            max_pages=1,
            session_date=session_date,
            now=dt.datetime(2026, 7, 10, 16, 1, 5, tzinfo=NEW_YORK),
        )

    assert observation.status == "오류: 장마감 마지막 완료 봉 없음"
    assert store.last_processed_bar("EOD") == dt.datetime(2026, 7, 10, 15, 58, tzinfo=NEW_YORK)


def test_eod_candidate_batch_retries_only_the_missing_last_bar(tmp_path: Path) -> None:
    session_date = dt.date(2026, 7, 10)
    store = _active_store(tmp_path / "paper.sqlite3", session_date)
    payloads = iter((_minute_payload("155800"), _minute_payload("155900")))
    requests = 0
    waits: list[float] = []

    def respond(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, json=next(payloads))

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(respond),
    ) as client:
        result = catch_up_candidates(
            KisPaperScanner(
                client,
                KisSession(KisCredentials("key", "secret"), "token"),
                _engine(store),
            ),
            (_stock(),),
            max_pages=1,
            session_date=session_date,
            observed_at=dt.datetime(2026, 7, 10, 16, 1, 5, tzinfo=NEW_YORK),
            last_bar_retry_delays_seconds=(2.0,),
            sleeper=waits.append,
        )

    assert result.complete_count == 1
    assert result.failure_count == 0
    assert requests == 2
    assert waits == [2.0]
    assert store.last_processed_bar("EOD") == dt.datetime(2026, 7, 10, 15, 59, tzinfo=NEW_YORK)


def test_eod_candidate_batch_preserves_failure_after_bounded_retries(tmp_path: Path) -> None:
    session_date = dt.date(2026, 7, 10)
    store = _active_store(tmp_path / "paper.sqlite3", session_date)
    requests = 0
    waits: list[float] = []

    def respond(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, json=_minute_payload("155800"))

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(respond),
    ) as client:
        result = catch_up_candidates(
            KisPaperScanner(
                client,
                KisSession(KisCredentials("key", "secret"), "token"),
                _engine(store),
            ),
            (_stock(),),
            max_pages=1,
            session_date=session_date,
            observed_at=dt.datetime(2026, 7, 10, 16, 1, 5, tzinfo=NEW_YORK),
            last_bar_retry_delays_seconds=(2.0, 5.0),
            sleeper=waits.append,
        )

    assert result.complete_count == 0
    assert result.failure_count == 1
    assert requests == 3
    assert waits == [2.0, 5.0]
    assert result.observations[0].status == "오류: 장마감 마지막 완료 봉 없음"


def test_tracked_candidates_can_be_loaded_by_session_after_the_close(tmp_path: Path) -> None:
    database = tmp_path / "paper.sqlite3"
    _ = PaperStore(database)
    session_date = dt.date(2026, 7, 10)
    _ = track_candidates(
        database,
        dt.datetime(2026, 7, 10, 15, 0, tzinfo=NEW_YORK),
        (_stock(),),
    )

    loaded = tracked_candidates_for_session(database, session_date)

    assert tuple((row.exchange, row.symbol) for row in loaded) == (("NAS", "EOD"),)


def _stock() -> KisRankedStock:
    return KisRankedStock(
        "NAS",
        "EOD",
        "EOD Corp",
        10.3,
        0.03,
        10.29,
        10.31,
        500_000,
        5_150_000.0,
        200_000,
        1,
    )


def _active_store(path: Path, session_date: dt.date) -> PaperStore:
    store = PaperStore(path)
    created_at = dt.datetime.combine(session_date, dt.time(15, 58), tzinfo=NEW_YORK)
    store.save(
        Recommendation(
            "eod-active",
            "EOD",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.ACTIVE,
            "fixture",
        )
    )
    store.set_last_processed_bar("EOD", created_at, 10.3)
    return store


def _engine(store: PaperStore) -> RecommendationEngine:
    return RecommendationEngine(
        MomentumScanner(ScannerConfig()),
        OpeningRangeBreakout(OrbConfig()),
        RiskConfig(),
        store,
    )


def _client(payload: KisMinutePayload) -> httpx2.Client:
    return httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload)),
    )


def _minute_payload(exchange_time: str) -> KisMinutePayload:
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": exchange_time,
                "kymd": "20260711",
                "khms": "045900" if exchange_time == "155900" else "045800",
                "open": "10.30",
                "high": "10.40",
                "low": "10.20",
                "last": "10.30",
                "evol": "50000",
                "eamt": "515000",
            }
        ],
    }
