from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import final

import httpx2

from scr_backtest.kis_intraday import KisApiError, KisSession
from trading_agent.bar_archive import CandidateBarBatch, archive_candidate_bars
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_daily import fetch_daily_context
from trading_agent.kis_live import completed_regular_minutes, session_is_fresh
from trading_agent.kis_provider import (
    KisRankedStock,
    fetch_latest_regular_session,
    ranking_to_bar_inputs,
)


@dataclass(frozen=True, slots=True)
class ScanObservation:
    exchange: str
    symbol: str
    change_pct: float
    price: float
    spread_bps: float
    bars: int
    status: str


@final
class KisPaperScanner:
    def __init__(
        self,
        client: httpx2.Client,
        session: KisSession,
        engine: RecommendationEngine,
    ) -> None:
        self.client = client
        self.session = session
        self.engine = engine

    def observe(
        self,
        stock: KisRankedStock,
        max_pages: int,
        now: dt.datetime | None = None,
    ) -> ScanObservation:
        try:
            minutes = fetch_latest_regular_session(
                self.client,
                self.session.credentials,
                self.session.access_token,
                stock,
                max_pages=max_pages,
            )
            if not minutes:
                return _observation(stock, 0, "분봉 없음")
            observed_at = dt.datetime.now().astimezone() if now is None else now
            completed = completed_regular_minutes(minutes, observed_at)
            _ = archive_candidate_bars(
                self.engine.store.path,
                CandidateBarBatch(
                    stock.exchange,
                    stock.symbol,
                    observed_at,
                    completed,
                ),
            )
            regular_bars = ranking_to_bar_inputs(stock, completed)
            if not session_is_fresh(completed, observed_at):
                return _observation(stock, len(regular_bars), "시장 폐장 또는 분봉 지연")
            if not math.isfinite(stock.spread_bps):
                return _observation(stock, len(regular_bars), "호가 없음")
            checkpoint = self.engine.store.last_processed_bar(stock.symbol)
            latest = completed[-1].exchange_timestamp
            if checkpoint is not None and latest <= checkpoint:
                return _observation(stock, len(regular_bars), "이미 처리한 봉")
            session_date = max(bar.exchange_timestamp.date() for bar in completed)
            daily = fetch_daily_context(
                self.client,
                self.session.credentials,
                self.session.access_token,
                stock.exchange,
                stock.symbol,
                session_date,
            )
            bars = ranking_to_bar_inputs(stock, completed, daily)
            _ = self.engine.process_forward(bars, observed_at)
            return _observation(stock, len(bars), "최신 완료 봉 평가")
        except (KisApiError, httpx2.HTTPError, ValueError) as error:
            message = " ".join(str(error).splitlines())
            return _observation(stock, 0, f"오류: {message}")

    def follow(
        self,
        stock: KisRankedStock,
        max_pages: int,
        now: dt.datetime | None = None,
    ) -> ScanObservation:
        try:
            minutes = fetch_latest_regular_session(
                self.client,
                self.session.credentials,
                self.session.access_token,
                stock,
                max_pages=max_pages,
            )
            if not minutes:
                return _observation(stock, 0, "분봉 없음")
            observed_at = dt.datetime.now().astimezone() if now is None else now
            completed = completed_regular_minutes(minutes, observed_at)
            _ = archive_candidate_bars(
                self.engine.store.path,
                CandidateBarBatch(
                    stock.exchange,
                    stock.symbol,
                    observed_at,
                    completed,
                ),
            )
            regular_bars = ranking_to_bar_inputs(stock, completed)
            if not session_is_fresh(completed, observed_at):
                return _observation(
                    stock,
                    len(regular_bars),
                    "시장 폐장 또는 분봉 지연",
                )
            if not self.engine.store.open_recommendations(stock.symbol):
                return _observation(stock, len(regular_bars), "추적 분봉 보존")
            session_date = max(bar.exchange_timestamp.date() for bar in completed)
            daily = fetch_daily_context(
                self.client,
                self.session.credentials,
                self.session.access_token,
                stock.exchange,
                stock.symbol,
                session_date,
            )
            bars = ranking_to_bar_inputs(stock, completed, daily)
            _ = self.engine.advance_forward(bars)
            return _observation(stock, len(bars), "추적 추천 상태 갱신")
        except (KisApiError, httpx2.HTTPError, ValueError) as error:
            message = " ".join(str(error).splitlines())
            return _observation(stock, 0, f"오류: {message}")


def _observation(stock: KisRankedStock, bars: int, status: str) -> ScanObservation:
    return ScanObservation(
        stock.exchange,
        stock.symbol,
        stock.change_pct,
        stock.price,
        stock.spread_bps,
        bars,
        status,
    )
