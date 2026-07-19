from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kr_intraday_market_gate import (
    KrDesignationState,
    KrHaltState,
    KrIntradayGateReason,
    KrMarketConstraintSnapshot,
    KrSessionState,
    KrTradingMode,
    KrViState,
)
from trading_agent.kr_theme_day_signal import (
    KrThemeDaySetup,
    project_kr_theme_day_shadow_signal,
)
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SignalActionability,
    SourceCoverage,
    TradeTarget,
)

OBSERVED = dt.datetime(2026, 7, 20, 1, 5, tzinfo=dt.UTC)


def test_eligible_leader_setup_projects_current_quote_validated_shadow_signal() -> None:
    decision = project_kr_theme_day_shadow_signal(
        _opportunity(),
        _market(),
        _setup(),
        evaluated_at=OBSERVED + dt.timedelta(seconds=2),
    )

    assert decision.gate_reasons == ()
    assert decision.signal is not None
    assert decision.signal.strategy_lane == KR_THEME_LEADER_VWAP_RECLAIM_LANE
    assert decision.signal.symbol == "005930"
    assert decision.signal.entry_price == Decimal("10500")
    assert decision.signal.stop_price == Decimal("10300")
    assert decision.signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert decision.signal.quote_validation is not None
    assert decision.signal.opportunity_id == _opportunity().opportunity_id


def test_blocked_market_preserves_reason_without_emitting_signal() -> None:
    market = _market().model_copy(update={"vi_state": KrViState.DYNAMIC_ACTIVE})

    decision = project_kr_theme_day_shadow_signal(
        _opportunity(),
        market,
        _setup(),
        evaluated_at=OBSERVED + dt.timedelta(seconds=2),
    )

    assert decision.signal is None
    assert decision.gate_reasons == (KrIntradayGateReason.VI_ACTIVE,)


def test_projection_rejects_non_leader_or_expired_setup() -> None:
    non_leader = _setup().model_copy(update={"symbol": "000660"})
    expired = _setup().model_copy(update={"valid_until": OBSERVED + dt.timedelta(seconds=1)})

    with pytest.raises(ValueError):
        _ = project_kr_theme_day_shadow_signal(
            _opportunity(),
            _market(),
            non_leader,
            evaluated_at=OBSERVED + dt.timedelta(seconds=2),
        )
    with pytest.raises(ValueError):
        _ = project_kr_theme_day_shadow_signal(
            _opportunity(),
            _market(),
            expired,
            evaluated_at=OBSERVED + dt.timedelta(seconds=2),
        )


def _opportunity() -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=OBSERVED - dt.timedelta(seconds=30),
        valid_until=OBSERVED + dt.timedelta(minutes=5),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("1000000000"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
            OpportunityCandidate(
                symbol="000660",
                rank=2,
                score=Decimal("900000000"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="kr/theme/state",
                record_id="theme-state-1",
                observed_at=OBSERVED - dt.timedelta(seconds=30),
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kr_theme",
                observed_at=OBSERVED - dt.timedelta(seconds=30),
                record_count=2,
                complete=True,
            ),
        ),
    )


def _setup() -> KrThemeDaySetup:
    return KrThemeDaySetup(
        setup_id="kr-theme-vwap-setup-001",
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        producer_strategy_version="kr-theme-leader-vwap-reclaim-v1",
        symbol="005930",
        observed_at=OBSERVED - dt.timedelta(seconds=1),
        valid_until=OBSERVED + dt.timedelta(seconds=30),
        stop_price=Decimal("10300"),
        targets=(
            TradeTarget(label="1r", price=Decimal("10700")),
            TradeTarget(label="2r", price=Decimal("10900")),
        ),
        max_slippage_bps=Decimal("20"),
        invalidation_rule="Invalidate below completed-bar VWAP support or when any KR market gate blocks.",
        rationale="Fresh theme leader completed-bar VWAP reclaim setup.",
        evidence_refs=(
            EvidenceRef(
                namespace="kr/day/setup",
                record_id="setup-source-1",
                observed_at=OBSERVED - dt.timedelta(seconds=1),
            ),
        ),
    )


def _market() -> KrMarketConstraintSnapshot:
    return KrMarketConstraintSnapshot(
        symbol="005930",
        observed_at=OBSERVED,
        previous_close=Decimal("10000"),
        last_price=Decimal("10490"),
        bid_price=Decimal("10490"),
        ask_price=Decimal("10500"),
        lower_limit_price=Decimal("7000"),
        upper_limit_price=Decimal("13000"),
        session_state=KrSessionState.OPEN,
        vi_state=KrViState.CLEAR,
        trading_mode=KrTradingMode.CONTINUOUS,
        halt_state=KrHaltState.CLEAR,
        designation_state=KrDesignationState.CLEAR,
        evidence_refs=(
            EvidenceRef(namespace="quote/kis-kr", record_id="quote-1", observed_at=OBSERVED),
            EvidenceRef(namespace="status/ls-kr", record_id="status-1", observed_at=OBSERVED),
        ),
    )
