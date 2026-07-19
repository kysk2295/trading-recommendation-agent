from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kr_intraday_market_gate import (
    KrDesignationState,
    KrHaltState,
    KrMarketConstraintSnapshot,
    KrSessionState,
    KrTradingMode,
    KrViState,
)
from trading_agent.kr_theme_day_setup import (
    KrCompletedMinuteBar,
    KrThemeDaySetupInput,
    derive_kr_theme_day_setup,
)
from trading_agent.kr_theme_day_signal import project_kr_theme_day_shadow_signal
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

SEOUL = dt.timezone(dt.timedelta(hours=9))
SESSION = dt.datetime(2026, 7, 20, 9, 0, tzinfo=SEOUL)


def test_first_pullback_vwap_reclaim_derives_setup_and_shadow_signal() -> None:
    setup = derive_kr_theme_day_setup(_setup_input())

    assert setup is not None
    assert setup.symbol == "005930"
    assert setup.opportunity_id == "KR-THEME-OPPORTUNITY-001"
    assert setup.observed_at == SESSION + dt.timedelta(minutes=4, seconds=1)
    assert setup.stop_price == Decimal("100")
    assert tuple(target.price for target in setup.targets) == (
        Decimal("106"),
        Decimal("109"),
    )
    assert tuple(item.canonical_id for item in setup.evidence_refs) == (
        "kr/minute/bar:bar-1",
        "kr/minute/bar:bar-2",
        "kr/minute/bar:bar-3",
        "kr/minute/bar:bar-4",
    )
    assert derive_kr_theme_day_setup(_setup_input()) == setup

    decision = project_kr_theme_day_shadow_signal(
        _opportunity(),
        _market(),
        setup,
        evaluated_at=SESSION + dt.timedelta(minutes=4, seconds=3),
    )
    assert decision.signal is not None
    assert decision.signal.entry_price == Decimal("103")


def test_missing_reclaim_returns_no_setup() -> None:
    bars = _bars()
    latest = bars[-1].model_copy(
        update={
            "high": Decimal("102"),
            "close": Decimal("101"),
            "trading_value_krw": Decimal("18180"),
        }
    )

    setup = derive_kr_theme_day_setup(_setup_input(bars=(*bars[:-1], latest)))

    assert setup is None


def test_intraday_opportunity_can_use_already_completed_session_bars() -> None:
    observed = SESSION + dt.timedelta(minutes=3, seconds=30)
    opportunity = _opportunity().model_copy(
        update={
            "observed_at": observed,
            "evidence_refs": (EvidenceRef(namespace="kr/theme/state", record_id="theme-1", observed_at=observed),),
            "source_coverage": (
                SourceCoverage(source_id="kr_theme", observed_at=observed, record_count=2, complete=True),
            ),
        }
    )
    request = _setup_input().model_copy(update={"opportunity": opportunity})

    setup = derive_kr_theme_day_setup(request)

    assert setup is not None
    assert setup.observed_at > opportunity.observed_at


@pytest.mark.parametrize("case", ("non_leader", "gap", "future_observation"))
def test_invalid_point_in_time_or_lineage_is_rejected(case: str) -> None:
    bars = _bars()
    request = _setup_input()
    if case == "non_leader":
        bars = tuple(bar.model_copy(update={"symbol": "000660"}) for bar in bars)
        request = _setup_input(bars=bars)
    elif case == "gap":
        shifted = bars[2].model_copy(
            update={
                "start_at": bars[2].start_at + dt.timedelta(minutes=1),
                "end_at": bars[2].end_at + dt.timedelta(minutes=1),
                "observed_at": bars[2].observed_at + dt.timedelta(minutes=1),
                "evidence_ref": bars[2].evidence_ref.model_copy(
                    update={"observed_at": bars[2].evidence_ref.observed_at + dt.timedelta(minutes=1)}
                ),
            }
        )
        request = _setup_input(bars=(bars[0], bars[1], shifted, bars[3]))
    else:
        request = request.model_copy(update={"evaluated_at": bars[-1].observed_at - dt.timedelta(seconds=1)})

    with pytest.raises(ValueError, match="KR theme day setup input is invalid"):
        _ = derive_kr_theme_day_setup(request)


def _setup_input(
    *,
    bars: tuple[KrCompletedMinuteBar, ...] | None = None,
) -> KrThemeDaySetupInput:
    return KrThemeDaySetupInput(
        opportunity=_opportunity(),
        bars=_bars() if bars is None else bars,
        producer_strategy_version="kr-theme-leader-vwap-reclaim-v1",
        evaluated_at=SESSION + dt.timedelta(minutes=4, seconds=2),
        max_slippage_bps=Decimal("20"),
    )


def _bars() -> tuple[KrCompletedMinuteBar, ...]:
    return (
        _bar(0, "100", "101", "99", "101", 100, "10000"),
        _bar(1, "101", "103", "100", "102", 100, "10100"),
        _bar(2, "102", "102", "100", "100.8", 100, "10080"),
        _bar(3, "101", "104", "101", "103", 180, "18360"),
    )


def _bar(
    minute: int,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: int,
    trading_value: str,
) -> KrCompletedMinuteBar:
    start = SESSION + dt.timedelta(minutes=minute)
    return KrCompletedMinuteBar(
        symbol="005930",
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=start + dt.timedelta(minutes=1, seconds=1),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        trading_value_krw=Decimal(trading_value),
        evidence_ref=EvidenceRef(
            namespace="kr/minute/bar",
            record_id=f"bar-{minute + 1}",
            observed_at=start + dt.timedelta(minutes=1, seconds=1),
        ),
    )


def _opportunity() -> OpportunitySnapshot:
    observed = SESSION - dt.timedelta(minutes=1)
    return OpportunitySnapshot(
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=observed,
        valid_until=SESSION + dt.timedelta(minutes=10),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("100"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
            OpportunityCandidate(
                symbol="000660",
                rank=2,
                score=Decimal("90"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="kr/theme/state", record_id="theme-1", observed_at=observed),),
        source_coverage=(SourceCoverage(source_id="kr_theme", observed_at=observed, record_count=2, complete=True),),
    )


def _market() -> KrMarketConstraintSnapshot:
    observed = SESSION + dt.timedelta(minutes=4, seconds=2)
    return KrMarketConstraintSnapshot(
        symbol="005930",
        observed_at=observed,
        previous_close=Decimal("95"),
        last_price=Decimal("103"),
        bid_price=Decimal("102.9"),
        ask_price=Decimal("103"),
        lower_limit_price=Decimal("66.5"),
        upper_limit_price=Decimal("123.5"),
        session_state=KrSessionState.OPEN,
        vi_state=KrViState.CLEAR,
        trading_mode=KrTradingMode.CONTINUOUS,
        halt_state=KrHaltState.CLEAR,
        designation_state=KrDesignationState.CLEAR,
        evidence_refs=(
            EvidenceRef(namespace="quote/kis-kr", record_id="quote-1", observed_at=observed),
            EvidenceRef(namespace="status/ls-kr", record_id="status-1", observed_at=observed),
        ),
    )
