from __future__ import annotations

import datetime as dt
from typing import final

from trading_agent.causality import first_eligible_bar_at
from trading_agent.kis_live import NEW_YORK, regular_session_bounds
from trading_agent.models import BarInput, Recommendation, RecommendationState
from trading_agent.risk import RiskConfig, build_trade_plan
from trading_agent.scanner import MomentumScanner
from trading_agent.store import PaperStore
from trading_agent.strategy_contract import IntradayStrategy


def finalize_due_recommendations(
    store: PaperStore,
    observed_at: dt.datetime,
) -> int:
    finalized = 0
    for recommendation in store.recommendations():
        if recommendation.state not in {
            RecommendationState.SETUP,
            RecommendationState.ACTIVE,
            RecommendationState.TARGET_1R,
        }:
            continue
        session_date = recommendation.created_at.astimezone(NEW_YORK).date()
        bounds = regular_session_bounds(session_date)
        checkpoint = store.last_processed_bar(recommendation.symbol)
        last_close = store.last_processed_close(recommendation.symbol)
        if (
            bounds is None
            or observed_at.astimezone(NEW_YORK) < bounds[1]
            or checkpoint is None
            or checkpoint.astimezone(NEW_YORK).date() != session_date
            or last_close is None
        ):
            continue
        store.set_state(
            recommendation.recommendation_id,
            RecommendationState.TIME_EXIT,
            bounds[1],
            last_close,
            f"장 마감 paper 종료 · 마지막 완료 봉 {checkpoint.isoformat()} 가격",
        )
        finalized += 1
    return finalized


@final
class RecommendationEngine:
    def __init__(
        self,
        scanner: MomentumScanner,
        strategy: IntradayStrategy,
        risk_config: RiskConfig,
        store: PaperStore,
    ) -> None:
        self.scanner = scanner
        self.strategy = strategy
        self.risk_config = risk_config
        self.store = store

    def process(
        self,
        bar: BarInput,
        available_at: dt.datetime | None = None,
    ) -> Recommendation | None:
        self._update_open_recommendations(bar)
        candidate = self.scanner.observe(bar)
        signal = self.strategy.observe(bar, candidate)
        if signal is None:
            return None
        if any(
            row.symbol == signal.symbol
            and row.strategy == signal.strategy
            and row.created_at.astimezone(NEW_YORK).date() == signal.timestamp.astimezone(NEW_YORK).date()
            for row in self.store.recommendations()
        ):
            return None
        plan = build_trade_plan(signal.entry, signal.stop, bar.spread_bps, self.risk_config)
        if plan is None:
            return None
        created_at = (
            signal.timestamp if available_at is None else max(signal.timestamp, available_at.astimezone(NEW_YORK))
        )
        recommendation = Recommendation(
            recommendation_id=(f"{created_at.isoformat()}:{signal.symbol}:{signal.strategy}"),
            symbol=signal.symbol,
            strategy=signal.strategy,
            created_at=created_at,
            entry=plan.entry,
            stop=plan.stop,
            target_1r=plan.target_1r,
            target_2r=plan.target_2r,
            state=RecommendationState.SETUP,
            rationale=signal.rationale,
        )
        self.store.save(recommendation)
        return recommendation

    def warmup(self, bar: BarInput) -> None:
        _ = self.scanner.observe(bar)
        _ = self.strategy.observe(bar, None)

    def advance(self, bar: BarInput) -> None:
        self._update_open_recommendations(bar)
        self.warmup(bar)

    def process_snapshot(self, bars: tuple[BarInput, ...]) -> Recommendation | None:
        if not bars:
            return None
        for bar in bars[:-1]:
            self.warmup(bar)
        return self.process(bars[-1])

    def process_forward(
        self,
        bars: tuple[BarInput, ...],
        available_at: dt.datetime | None = None,
    ) -> Recommendation | None:
        if not bars:
            return None
        symbol = bars[-1].symbol
        checkpoint = self.store.last_processed_bar(symbol)
        history = () if checkpoint is None else tuple(bar for bar in bars if bar.timestamp <= checkpoint)
        for bar in history:
            self.warmup(bar)
        pending = tuple(bar for bar in bars if checkpoint is None or bar.timestamp > checkpoint)
        if not pending:
            return None
        for bar in pending[:-1]:
            self.advance(bar)
        recommendation = self.process(pending[-1], available_at)
        self.store.set_last_processed_bar(
            symbol,
            pending[-1].timestamp,
            pending[-1].close,
        )
        return recommendation

    def advance_forward(self, bars: tuple[BarInput, ...]) -> int:
        if not bars:
            return 0
        symbol = bars[-1].symbol
        checkpoint = self.store.last_processed_bar(symbol)
        history = () if checkpoint is None else tuple(bar for bar in bars if bar.timestamp <= checkpoint)
        for bar in history:
            self.warmup(bar)
        pending = tuple(bar for bar in bars if checkpoint is None or bar.timestamp > checkpoint)
        for bar in pending:
            self.advance(bar)
        if pending:
            self.store.set_last_processed_bar(
                symbol,
                pending[-1].timestamp,
                pending[-1].close,
            )
        return len(pending)

    def finalize_day(self, bar: BarInput) -> None:
        for recommendation in self.store.open_recommendations(bar.symbol):
            self.store.set_state(
                recommendation.recommendation_id,
                RecommendationState.TIME_EXIT,
                bar.timestamp,
                bar.close,
                "장 마감 paper 종료",
            )

    def _update_open_recommendations(self, bar: BarInput) -> None:
        for recommendation in self.store.open_recommendations(bar.symbol):
            if bar.timestamp.astimezone(NEW_YORK) < first_eligible_bar_at(recommendation.created_at):
                continue
            state = recommendation.state
            if state is RecommendationState.SETUP:
                if bar.high < recommendation.entry:
                    if bar.low <= recommendation.stop:
                        self.store.set_state(
                            recommendation.recommendation_id,
                            RecommendationState.INVALIDATED,
                            bar.timestamp,
                            bar.close,
                            "진입 전 무효화 가격 도달",
                        )
                    continue
                self.store.set_state(
                    recommendation.recommendation_id,
                    RecommendationState.ACTIVE,
                    bar.timestamp,
                    max(recommendation.entry, bar.open),
                    "조건부 진입가 도달",
                )
                state = RecommendationState.ACTIVE
            if state not in {
                RecommendationState.ACTIVE,
                RecommendationState.TARGET_1R,
            }:
                continue
            if bar.low <= recommendation.stop:
                self.store.set_state(
                    recommendation.recommendation_id,
                    RecommendationState.STOPPED,
                    bar.timestamp,
                    recommendation.stop,
                    "손절가 도달",
                )
            elif bar.high >= recommendation.target_2r:
                self.store.set_state(
                    recommendation.recommendation_id,
                    RecommendationState.TARGET_2R,
                    bar.timestamp,
                    max(recommendation.target_2r, bar.open),
                    "2R 목표가 도달",
                )
            elif state is RecommendationState.ACTIVE and bar.high >= recommendation.target_1r:
                self.store.set_state(
                    recommendation.recommendation_id,
                    RecommendationState.TARGET_1R,
                    bar.timestamp,
                    max(recommendation.target_1r, bar.open),
                    "1R 목표가 도달",
                )
