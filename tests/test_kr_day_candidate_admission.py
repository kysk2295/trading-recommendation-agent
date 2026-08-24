from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError

from trading_agent.kr_day_candidate_admission import (
    InvalidKrDayCandidateAdmissionError,
    KrDayCandidateAdmissionPolicy,
    KrDayCandidateAdmissionRequest,
    assess_kr_day_candidate_admission,
    kr_day_candidate_thesis_key,
)
from trading_agent.kr_day_decision_models import (
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
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
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

SEOUL = dt.timezone(dt.timedelta(hours=9))
SESSION = dt.datetime(2026, 8, 24, 9, 0, tzinfo=SEOUL)
HEX_A = "a" * 64
HEX_B = "b" * 64


def test_passing_candidate_is_admitted_only_for_further_research() -> None:
    result = assess_kr_day_candidate_admission(_request())

    assert result.admitted is True
    assert result.status is KrDayDecisionStatus.INVESTIGATING
    assert result.reason_codes == ()
    assert tuple(item.name for item in result.observed_evidence) == tuple(
        sorted(item.name for item in result.observed_evidence)
    )
    assert result.source_evidence_refs == tuple(sorted(set(result.source_evidence_refs)))


@pytest.mark.parametrize(
    ("feature", "value", "reason"),
    [
        ("theme_related_symbol_count", "1", KrDayDecisionReasonCode.THEME_BREADTH_MISSING),
        ("theme_publisher_count", "0", KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING),
        ("volume_ratio", "1.0", KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING),
        ("trading_value_krw", "1", KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING),
    ],
)
def test_each_candidate_feature_gate_rejects(feature: str, value: str, reason: KrDayDecisionReasonCode) -> None:
    result = assess_kr_day_candidate_admission(_request(features={feature: value}))

    assert result.admitted is False
    assert result.status is KrDayDecisionStatus.REJECTED
    assert reason in result.reason_codes


def test_observed_005930_shape_cannot_claim_armed_or_active() -> None:
    request = _request(
        features={
            "theme_catalyst_count": "0",
            "theme_publisher_count": "0",
            "theme_related_symbol_count": "1",
            "volume_ratio": "1.0",
        },
        bars=_bars(neutral=True),
    )

    result = assess_kr_day_candidate_admission(request)

    assert result.admitted is False
    assert result.status is KrDayDecisionStatus.REJECTED
    assert result.reason_codes == (
        KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING,
        KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING,
        KrDayDecisionReasonCode.THEME_BREADTH_MISSING,
        KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING,
    )
    assert result.status not in {KrDayDecisionStatus.ARMED}


def test_missing_or_malformed_features_remain_explicit_evidence() -> None:
    missing = assess_kr_day_candidate_admission(
        _request(features={"theme_catalyst_count": None, "volume_ratio": "not-a-decimal"})
    )

    assert {
        KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING,
        KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING,
    }.issubset(missing.reason_codes)
    values = {item.name: item.value for item in missing.observed_evidence}
    assert values["theme_catalyst_count"] == "missing"
    assert values["volume_ratio"] == "malformed"


def test_completed_bar_volume_and_price_response_are_required() -> None:
    low_volume = assess_kr_day_candidate_admission(_request(bars=_bars(low_volume=True)))
    neutral_price = assess_kr_day_candidate_admission(_request(bars=_bars(neutral=True)))

    assert KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING in low_volume.reason_codes
    assert KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING in neutral_price.reason_codes


def test_neutral_price_response_cannot_pass_a_positive_policy_threshold() -> None:
    request = _request(bars=_bars(neutral=True)).model_copy(
        update={"policy": _policy(min_completed_bar_price_response=Decimal("0.000001"))}
    )

    result = assess_kr_day_candidate_admission(request)

    assert KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING in result.reason_codes


@pytest.mark.parametrize(
    "bars",
    (
        lambda: _bars()[:-1],
        lambda: (_bar(0, close="100", volume=100), _bar(2, close="103", volume=200)),
        lambda: tuple(reversed(_bars())),
        lambda: (_bar(0, close="100", volume=100, session=SESSION - dt.timedelta(days=1)), *_bars()[1:]),
        lambda: _bars()[1:],
    ),
)
def test_noncurrent_or_noncontiguous_completed_bar_chain_fails_closed(
    bars: Callable[[], tuple[KrCompletedMinuteBar, ...]],
) -> None:
    result = assess_kr_day_candidate_admission(_request(bars=bars()))

    assert result.admitted is False
    assert result.status is KrDayDecisionStatus.BLOCKED
    assert KrDayDecisionReasonCode.STALE_EVIDENCE in result.reason_codes


def test_order_book_imbalance_feature_cannot_satisfy_flow() -> None:
    result = assess_kr_day_candidate_admission(
        _request(features={"order_book_imbalance": "0.99"}, bars=_bars(neutral=True))
    )

    assert KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING in result.reason_codes


def test_spread_and_market_gate_blocks_are_explicit() -> None:
    wide = assess_kr_day_candidate_admission(_request(market=_market(bid=Decimal("100"), ask=Decimal("101"))))
    stale = assess_kr_day_candidate_admission(_request(market=_market(observed_at=SESSION + dt.timedelta(minutes=1))))
    vi = assess_kr_day_candidate_admission(_request(market=_market(vi_state=KrViState.STATIC_ACTIVE)))
    crossed = assess_kr_day_candidate_admission(_request(market=_market(bid=Decimal("103.1"), ask=Decimal("103"))))

    assert wide.reason_codes == (KrDayDecisionReasonCode.SPREAD_TOO_WIDE,)
    assert KrDayDecisionReasonCode.STALE_EVIDENCE in stale.reason_codes
    assert KrDayDecisionReasonCode.MARKET_GATE_BLOCKED in stale.reason_codes
    assert KrDayDecisionReasonCode.MARKET_GATE_BLOCKED in vi.reason_codes
    assert KrDayDecisionReasonCode.MARKET_GATE_BLOCKED in crossed.reason_codes


def test_duplicate_thesis_is_stable_across_opportunity_and_capsule() -> None:
    initial = _request()
    thesis_key = kr_day_candidate_thesis_key(initial.opportunity, "005930")
    duplicate = assess_kr_day_candidate_admission(_request(active_thesis_keys=(thesis_key,)))

    assert duplicate.admitted is False
    assert duplicate.status is KrDayDecisionStatus.REJECTED
    assert duplicate.reason_codes == (KrDayDecisionReasonCode.DUPLICATE_THESIS,)
    assert thesis_key == kr_day_candidate_thesis_key(
        _request(opportunity_id="ANOTHER-OPPORTUNITY").opportunity,
        "005930",
    )
    with pytest.raises(InvalidKrDayCandidateAdmissionError):
        kr_day_candidate_thesis_key(initial.opportunity, "000660")


def test_policy_identity_binds_every_threshold_and_version() -> None:
    policy = _policy()
    changed = _policy(min_publisher_count=2)

    assert policy.policy_id == KrDayCandidateAdmissionPolicy.canonical_id_for(policy)
    assert changed.policy_id == KrDayCandidateAdmissionPolicy.canonical_id_for(changed)
    assert changed.policy_id != policy.policy_id
    with pytest.raises(ValidationError):
        KrDayCandidateAdmissionPolicy.model_validate(
            policy.model_dump(mode="python") | {"min_publisher_count": 2}
        )
    with pytest.raises(ValidationError):
        _policy(min_completed_bar_price_response=Decimal(0))


def test_policy_identity_mismatch_and_malformed_boundary_fail_closed() -> None:
    request = _request()
    mismatch = request.model_copy(update={"policy": _policy(capsule_id="c" * 64), "capsule_id": HEX_A})
    hypothesis_mismatch = request.model_copy(
        update={"policy": _policy(hypothesis_version_id="d" * 64), "hypothesis_version_id": HEX_B}
    )

    with pytest.raises(ValidationError):
        _policy(min_related_symbol_count=0)
    with pytest.raises(InvalidKrDayCandidateAdmissionError):
        assess_kr_day_candidate_admission(mismatch)
    with pytest.raises(InvalidKrDayCandidateAdmissionError):
        assess_kr_day_candidate_admission(hypothesis_mismatch)
    with pytest.raises(InvalidKrDayCandidateAdmissionError):
        assess_kr_day_candidate_admission(KrDayCandidateAdmissionRequest.model_construct())


def test_expired_and_future_completed_bar_evidence_fail_closed() -> None:
    expired = assess_kr_day_candidate_admission(_request(valid_until=SESSION + dt.timedelta(minutes=2, seconds=30)))
    future = assess_kr_day_candidate_admission(_request(bars=(*_bars()[:-1], _bar(3, close="103", volume=200))))

    assert expired.status is KrDayDecisionStatus.EXPIRED
    assert KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED in expired.reason_codes
    assert future.admitted is False
    assert KrDayDecisionReasonCode.STALE_EVIDENCE in future.reason_codes


def test_result_evidence_is_immutable_and_canonical() -> None:
    result = assess_kr_day_candidate_admission(_request())

    with pytest.raises(ValidationError):
        result.__setattr__("admitted", False)
    with pytest.raises(ValidationError):
        result.observed_evidence[0].__setattr__("value", "changed")
    assert assess_kr_day_candidate_admission(_request()) == result


def _request(
    *,
    features: dict[str, str | None] | None = None,
    bars: tuple[KrCompletedMinuteBar, ...] | None = None,
    market: KrMarketConstraintSnapshot | None = None,
    active_thesis_keys: tuple[str, ...] = (),
    valid_until: dt.datetime | None = None,
    opportunity_id: str = "KR-THEME-OPPORTUNITY",
) -> KrDayCandidateAdmissionRequest:
    values = {
        "is_leader": "true",
        "theme_name": "semiconductor",
        "theme_catalyst_count": "2",
        "theme_publisher_count": "2",
        "theme_related_symbol_count": "3",
        "trading_value_krw": "50000000",
        "volume_ratio": "1.5",
    }
    for name, value in (features or {}).items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    observed = SESSION + dt.timedelta(minutes=3, seconds=2)
    opportunity = OpportunitySnapshot(
        opportunity_id=opportunity_id,
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=SESSION + dt.timedelta(minutes=2),
        valid_until=valid_until or SESSION + dt.timedelta(minutes=8),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("100"),
                features=tuple(FeatureValue(name=name, value=value) for name, value in sorted(values.items())),
            ),
        ),
        evidence_refs=(
            EvidenceRef(namespace="kr/theme", record_id="theme-1", observed_at=SESSION + dt.timedelta(minutes=2)),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kr_theme", observed_at=SESSION + dt.timedelta(minutes=2), record_count=1, complete=True
            ),
        ),
    )
    return KrDayCandidateAdmissionRequest(
        policy=_policy(),
        capsule_id=HEX_A,
        hypothesis_version_id=HEX_B,
        opportunity=opportunity,
        market=market or _market(),
        bars=bars or _bars(),
        evaluated_at=observed,
        active_thesis_keys=active_thesis_keys,
    )


def _policy(
    *,
    policy_version: Literal["kr-day-candidate-admission-v1"] = "kr-day-candidate-admission-v1",
    capsule_id: str = HEX_A,
    hypothesis_version_id: str = HEX_B,
    min_related_symbol_count: int = 2,
    min_catalyst_count: int = 1,
    min_publisher_count: int = 1,
    min_opportunity_volume_ratio: Decimal = Decimal("1.2"),
    min_completed_bar_volume_ratio: Decimal = Decimal("1.2"),
    min_trading_value_krw: Decimal = Decimal("1000000"),
    min_completed_bar_trading_value_krw: Decimal = Decimal("10000"),
    min_completed_bar_price_response: Decimal = Decimal("0.005"),
    max_spread_bps: Decimal = Decimal("20"),
) -> KrDayCandidateAdmissionPolicy:
    draft = KrDayCandidateAdmissionPolicy.model_construct(
        policy_version=policy_version,
        policy_id="0" * 64,
        capsule_id=capsule_id,
        hypothesis_version_id=hypothesis_version_id,
        min_related_symbol_count=min_related_symbol_count,
        min_catalyst_count=min_catalyst_count,
        min_publisher_count=min_publisher_count,
        min_opportunity_volume_ratio=min_opportunity_volume_ratio,
        min_completed_bar_volume_ratio=min_completed_bar_volume_ratio,
        min_trading_value_krw=min_trading_value_krw,
        min_completed_bar_trading_value_krw=min_completed_bar_trading_value_krw,
        min_completed_bar_price_response=min_completed_bar_price_response,
        max_spread_bps=max_spread_bps,
    )
    return KrDayCandidateAdmissionPolicy.model_validate(
        draft.model_dump(mode="python") | {"policy_id": KrDayCandidateAdmissionPolicy.canonical_id_for(draft)}
    )


def _bars(*, low_volume: bool = False, neutral: bool = False) -> tuple[KrCompletedMinuteBar, ...]:
    if neutral:
        return (_bar(0, close="100", volume=100), _bar(1, close="100", volume=100), _bar(2, close="100", volume=200))
    volume = 100 if low_volume else 200
    return (_bar(0, close="100", volume=100), _bar(1, close="102", volume=100), _bar(2, close="103", volume=volume))


def _bar(
    index: int,
    *,
    close: str,
    volume: int,
    end_offset: int | None = None,
    session: dt.datetime = SESSION,
) -> KrCompletedMinuteBar:
    start = session + dt.timedelta(minutes=index)
    end = session + dt.timedelta(minutes=end_offset if end_offset is not None else index + 1)
    price = Decimal(close)
    return KrCompletedMinuteBar(
        symbol="005930",
        start_at=start,
        end_at=end,
        observed_at=end,
        open=Decimal("100"),
        high=max(Decimal("103"), price),
        low=Decimal("99"),
        close=price,
        volume=volume,
        trading_value_krw=price * Decimal(volume),
        evidence_ref=EvidenceRef(namespace="kr/minute", record_id=f"bar-{index}", observed_at=end),
    )


def _market(
    *,
    bid: Decimal = Decimal("102.9"),
    ask: Decimal = Decimal("103"),
    observed_at: dt.datetime | None = None,
    vi_state: KrViState = KrViState.CLEAR,
) -> KrMarketConstraintSnapshot:
    observed = observed_at or SESSION + dt.timedelta(minutes=3)
    return KrMarketConstraintSnapshot(
        symbol="005930",
        observed_at=observed,
        previous_close=Decimal("95"),
        last_price=Decimal("103"),
        bid_price=bid,
        ask_price=ask,
        lower_limit_price=Decimal("66.5"),
        upper_limit_price=Decimal("123.5"),
        session_state=KrSessionState.OPEN,
        vi_state=vi_state,
        trading_mode=KrTradingMode.CONTINUOUS,
        halt_state=KrHaltState.CLEAR,
        designation_state=KrDesignationState.CLEAR,
        evidence_refs=(EvidenceRef(namespace="kr/quote", record_id="quote-1", observed_at=observed),),
    )
