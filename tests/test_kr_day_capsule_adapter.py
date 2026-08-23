from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.day_strategy_capsule_support import builtin_capsule
from trading_agent.day_strategy_capsule_models import CapsuleAuthorityCeiling
from trading_agent.kis_kr_session_calendar_models import (
    KIS_CALENDAR_ADAPTER_VERSION,
    KIS_CALENDAR_SOURCE_COMMIT,
    KrSessionCalendarPayload,
    KrSessionDay,
    kr_session_calendar_snapshot,
)
from trading_agent.kr_day_capsule_adapter import (
    InvalidKrDayCapsuleEvaluationError,
    adapt_kr_day_capsule_evaluation,
)
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_intraday_market_gate import (
    KrDesignationState,
    KrHaltState,
    KrMarketConstraintSnapshot,
    KrSessionState,
    KrTradingMode,
    KrViState,
)
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

KST = dt.timezone(dt.timedelta(hours=9))
EVALUATED = dt.datetime(2026, 8, 24, 10, 2, 2, tzinfo=KST)
SESSION_DATE = EVALUATED.date()
SYMBOL = "005930"
CYCLE = "kr-cycle-20260824-1000"
type _UnsafeRequestValue = BaseModel | dt.datetime | tuple[KrCompletedMinuteBar, ...]


def test_current_official_session_projects_deterministic_research_only_evaluation() -> None:
    request = _request()

    first = adapt_kr_day_capsule_evaluation(request)
    second = adapt_kr_day_capsule_evaluation(request)

    assert first == second
    assert first.session_date == EVALUATED.date()
    assert first.collection_cycle_id == CYCLE
    assert first.completed_bar_cursor == _bars()[-1].end_at
    assert first.setup_input.producer_strategy_version == request.capsule.capsule_id
    assert first.authority_ceiling is CapsuleAuthorityCeiling.RESEARCH_ONLY
    assert first.trading_authority is False


@pytest.mark.parametrize("capsule_kind", ("us", "wrong_authority"))
def test_non_kr_or_wrong_authority_capsule_is_rejected(capsule_kind: str) -> None:
    capsule = builtin_capsule(market_id=MarketId.US_EQUITIES)
    if capsule_kind == "wrong_authority":
        capsule_payload = {name: getattr(capsule, name) for name in type(capsule).model_fields}
        unsafe_authority = {
            "market_id": MarketId.KR_EQUITIES,
            "authority_ceiling": CapsuleAuthorityCeiling.US_ALPACA_PAPER_CAPABLE,
        }
        capsule = capsule.model_construct(**(capsule_payload | unsafe_authority))

    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(capsule=capsule))


def test_non_kr_symbol_or_missing_same_cycle_lineage_is_rejected() -> None:
    wrong_symbol = _market().model_copy(update={"symbol": "AAPL"})
    missing_cycle = _opportunity().model_copy(update={"evidence_refs": ()})
    leader = _opportunity().candidates[0]
    missing_flow = _opportunity().model_copy(
        update={"candidates": (leader.model_copy(update={"features": leader.features[:-1]}),)}
    )

    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(market=wrong_symbol))
    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(opportunity=missing_cycle))
    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(opportunity=missing_flow))


@pytest.mark.parametrize("calendar_kind", ("missing", "closed", "stale", "future"))
def test_missing_stale_or_future_official_calendar_is_rejected(calendar_kind: str) -> None:
    calendar = _calendar()
    if calendar_kind == "missing":
        payload = calendar.payload.model_construct(
            schema_version=calendar.payload.schema_version,
            source_commit=calendar.payload.source_commit,
            adapter_version=calendar.payload.adapter_version,
            base_date=calendar.payload.base_date,
            observed_at=calendar.payload.observed_at,
            receipt_sha256=calendar.payload.receipt_sha256,
            days=(),
        )
        calendar = calendar.model_construct(snapshot_id=calendar.snapshot_id, payload=payload)
    elif calendar_kind == "closed":
        calendar = _calendar(open_day=False)
    elif calendar_kind == "stale":
        prior = EVALUATED.date() - dt.timedelta(days=1)
        calendar = _calendar(base_date=prior, observed_at=dt.datetime.combine(prior, dt.time(9), KST))
    else:
        calendar = _calendar(observed_at=EVALUATED + dt.timedelta(seconds=1))

    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(calendar=calendar))


def test_naive_evaluation_time_is_rejected() -> None:
    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(evaluated_at=EVALUATED.replace(tzinfo=None)))


@pytest.mark.parametrize("bar_kind", ("future", "incomplete", "noncontiguous"))
def test_future_incomplete_or_noncontiguous_completed_bar_chain_is_rejected(bar_kind: str) -> None:
    bars = _bars()
    if bar_kind == "future":
        bars = (*bars[:-1], bars[-1].model_copy(update={"observed_at": EVALUATED + dt.timedelta(seconds=1)}))
    elif bar_kind == "incomplete":
        bars = (*bars[:-1], bars[-1].model_copy(update={"end_at": EVALUATED + dt.timedelta(minutes=1)}))
    else:
        bars = (*bars[:-1], bars[-1].model_copy(update={"start_at": bars[-1].start_at + dt.timedelta(minutes=1)}))

    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(bars=bars))


@pytest.mark.parametrize("market_kind", ("stale", "crossed", "vi"))
def test_stale_crossed_or_constrained_quote_is_rejected(market_kind: str) -> None:
    market = _market()
    if market_kind == "stale":
        market = market.model_copy(update={"observed_at": EVALUATED - dt.timedelta(seconds=6)})
    elif market_kind == "crossed":
        market = market.model_copy(update={"bid_price": Decimal("10510")})
    else:
        market = market.model_copy(update={"vi_state": KrViState.DYNAMIC_ACTIVE})

    with pytest.raises(InvalidKrDayCapsuleEvaluationError):
        _ = adapt_kr_day_capsule_evaluation(_unsafe_request(market=market))


def test_adapter_import_closure_contains_no_mutation_authority() -> None:
    project = Path(__file__).resolve().parents[1]
    modules = (project / "trading_agent/kr_day_capsule_models.py", project / "trading_agent/kr_day_capsule_adapter.py")

    imports = {
        alias.name
        for module in modules
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any(token in name.lower() for name in imports for token in ("alpaca", "broker", "order", "account"))


def _request() -> KrDayCapsuleEvaluationRequest:
    return KrDayCapsuleEvaluationRequest(
        capsule=builtin_capsule(market_id=MarketId.KR_EQUITIES),
        calendar=_calendar(),
        opportunity=_opportunity(),
        market=_market(),
        bars=_bars(),
        evaluated_at=EVALUATED,
        max_slippage_bps=Decimal("20"),
    )


def _unsafe_request(**updates: _UnsafeRequestValue) -> KrDayCapsuleEvaluationRequest:
    request = _request()
    payload = {name: getattr(request, name) for name in type(request).model_fields}
    payload.update(updates)
    return KrDayCapsuleEvaluationRequest.model_construct(**payload)


def _calendar(
    *,
    base_date: dt.date = SESSION_DATE,
    observed_at: dt.datetime | None = None,
    open_day: bool = True,
):
    observed = EVALUATED - dt.timedelta(minutes=62) if observed_at is None else observed_at
    payload = KrSessionCalendarPayload(
        source_commit=KIS_CALENDAR_SOURCE_COMMIT,
        adapter_version=KIS_CALENDAR_ADAPTER_VERSION,
        base_date=base_date,
        observed_at=observed,
        receipt_sha256="c" * 64,
        days=(
            KrSessionDay(
                session_date=base_date,
                weekday_code="1",
                business_day=open_day,
                trading_day=open_day,
                open_day=open_day,
                settlement_day=open_day,
            ),
        ),
    )
    return kr_session_calendar_snapshot(payload)


def _opportunity() -> OpportunitySnapshot:
    observed = EVALUATED - dt.timedelta(minutes=2)
    return OpportunitySnapshot(
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=observed,
        valid_until=EVALUATED + dt.timedelta(minutes=3),
        candidates=(
            OpportunityCandidate(
                symbol=SYMBOL,
                rank=1,
                score=Decimal("1000000000"),
                features=(
                    FeatureValue(name="is_leader", value="true"),
                    FeatureValue(name="theme_name", value="semiconductor"),
                    FeatureValue(name="trading_value_krw", value="1000000000"),
                    FeatureValue(name="volume_ratio", value="2.5"),
                ),
            ),
        ),
        evidence_refs=(
            EvidenceRef(namespace="kr/collection_cycle", record_id=CYCLE, observed_at=observed),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kr_kis_ranking",
                observed_at=observed,
                record_count=1,
                complete=True,
            ),
        ),
    )


def _market() -> KrMarketConstraintSnapshot:
    observed = EVALUATED - dt.timedelta(seconds=1)
    return KrMarketConstraintSnapshot(
        symbol=SYMBOL,
        observed_at=observed,
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
        evidence_refs=(EvidenceRef(namespace="quote/kis-kr", record_id="quote-1", observed_at=observed),),
    )


def _bars() -> tuple[KrCompletedMinuteBar, ...]:
    session_open = dt.datetime(2026, 8, 24, 9, 0, tzinfo=KST)
    starts = tuple(session_open + dt.timedelta(minutes=index) for index in range(62))
    return tuple(_bar(start, index) for index, start in enumerate(starts))


def _bar(start: dt.datetime, index: int) -> KrCompletedMinuteBar:
    observed = start + dt.timedelta(minutes=1)
    return KrCompletedMinuteBar(
        symbol=SYMBOL,
        start_at=start,
        end_at=observed,
        observed_at=observed,
        open=Decimal("10000") + index,
        high=Decimal("10100") + index,
        low=Decimal("9900") + index,
        close=Decimal("10050") + index,
        volume=100,
        trading_value_krw=Decimal("1000000"),
        evidence_ref=EvidenceRef(namespace="bar/kis-kr", record_id=f"bar-{index}", observed_at=observed),
    )
