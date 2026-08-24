from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.kr_day_shadow_support import run_authorized_kr_shadow_tick
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation, _rebuild
from tests.test_kr_day_capsule_shadow_cli import _request_for
from tests.test_kr_live_decision_contract import _admitted_pullback_request, _with_admission_features
from trading_agent.kr_day_capsule_adapter import adapt_kr_day_capsule_evaluation
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation, KrDayCapsuleEvaluationPayload
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_service import (
    InvalidKrDayCapsuleShadowServiceError,
    run_kr_day_capsule_shadow_tick,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_service import run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_intraday_market_gate import (
    KrHaltState,
    KrIntradayGateReason,
    KrTradingMode,
    KrViState,
)


def test_armed_pending_decision_registers_without_fill(tmp_path: Path) -> None:
    # Given: an exact current ARMED decision whose conditional trigger is pending.
    request = _admitted_pullback_request()
    evaluation = adapt_kr_day_capsule_evaluation(request)
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    _ = run_kr_day_decision_tick((request,), decisions)

    # When: the shadow service consumes the immutable decision authority.
    result = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (evaluation,),
        decisions,
    ).results[0]

    # Then: pending remains explicitly REGISTERED and has no fill.
    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.reason is KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING
    assert result.event.entry_price is None
    assert result.decision_reason_codes == (KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,)


def test_exact_confirmed_armed_decision_creates_active_and_replays(tmp_path: Path) -> None:
    # Given: an admitted reclaim request and its just-persisted exact ARMED decision.
    request = _with_admission_features(_request_for(_entry_evaluation()))
    evaluation = adapt_kr_day_capsule_evaluation(request)
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    _ = run_kr_day_decision_tick((request,), decisions)
    shadows = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")

    # When: the exact tick is executed twice.
    first = run_kr_day_capsule_shadow_tick(shadows, (evaluation,), decisions).results[0]
    replay = run_kr_day_capsule_shadow_tick(shadows, (evaluation,), decisions).results[0]

    # Then: only the shadow owner creates one slippage-bearing ACTIVE event.
    assert first.created is True
    assert first.event.status is KrDayCapsuleShadowStatus.ACTIVE
    assert first.event.reason is KrDayCapsuleShadowReason.ENTRY
    assert first.event.entry_price is not None
    assert first.decision_reason_codes == (KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,)
    assert replay.created is False
    assert replay.event.event_id == first.event.event_id
    assert len(shadows.events()) == 1


def test_missing_decision_never_fills(tmp_path: Path) -> None:
    # Given: a fill-shaped evaluation with an empty decision store.
    evaluation = _entry_evaluation()

    # When: shadow execution has no admitted authority.
    result = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (evaluation,),
        KrDayDecisionStore(tmp_path / "decisions.sqlite3"),
    ).results[0]

    # Then: it fails closed before fill.
    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.reason is KrDayCapsuleShadowReason.DECISION_MISSING
    assert result.event.entry_price is None


def test_plural_admission_reasons_survive_joined_projection(tmp_path: Path) -> None:
    request = _request_for(_entry_evaluation())
    evaluation = adapt_kr_day_capsule_evaluation(request)
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    decision = run_kr_day_decision_tick((request,), decisions)[0]

    result = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (evaluation,),
        decisions,
    ).results[0]

    assert len(decision.reason_codes) > 1
    assert result.decision_reason_codes == decision.reason_codes
    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.entry_price is None


def test_expired_exact_decision_never_fills(tmp_path: Path) -> None:
    evaluation = _entry_evaluation()
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    payload = KrDayDecisionEventPayload(
        capsule_id=evaluation.capsule_id,
        hypothesis_version_id=evaluation.hypothesis_version_id,
        opportunity_id=evaluation.opportunity_id,
        session_date=evaluation.session_date,
        symbol=evaluation.symbol,
        completed_bar_at=evaluation.completed_bar_cursor,
        observed_at=evaluation.evaluated_at,
        valid_until=evaluation.completed_bar_cursor,
        status=KrDayDecisionStatus.EXPIRED,
        reason_codes=(KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED,),
        conditional_plan=None,
        evidence_refs=(evaluation.setup_input.bars[-1].evidence_ref.canonical_id,),
        observed_evidence=(
            KrDayDecisionEvidenceValue(
                name="decision_input_sha256",
                value=evaluation.decision_input_sha256,
            ),
        ),
    )
    decision = KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )
    assert decisions.append(decision)

    result = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (evaluation,),
        decisions,
    ).results[0]

    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.entry_price is None
    assert result.decision_reason_codes == (KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED,)


def test_mismatched_request_sha_never_fills(tmp_path: Path) -> None:
    request = _with_admission_features(_request_for(_entry_evaluation()))
    evaluation = adapt_kr_day_capsule_evaluation(request)
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    decision = run_kr_day_decision_tick((request,), decisions)[0]
    values = evaluation.model_dump(mode="python", exclude={"evaluation_id"})
    values["decision_input_sha256"] = "f" * 64
    payload = KrDayCapsuleEvaluationPayload.model_validate(values)
    mismatched = KrDayCapsuleEvaluation.model_validate(
        values | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(payload)}
    )

    result = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (mismatched,),
        decisions,
    ).results[0]

    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.reason is KrDayCapsuleShadowReason.DECISION_MISMATCH
    assert result.event.entry_price is None
    assert result.decision_event_id == decision.event_id
    replay = run_kr_day_capsule_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (mismatched,),
        decisions,
    ).results[0]
    assert replay.created is False
    assert replay.decision_event_id == result.decision_event_id
    assert replay.decision_reason_codes == result.decision_reason_codes
    assert replay.market_gate_reasons == result.market_gate_reasons


def test_old_evaluation_replay_keeps_exact_decision_metadata_after_later_decision(
    tmp_path: Path,
) -> None:
    first_request = _with_admission_features(_request_for(_entry_evaluation()))
    first_evaluation = adapt_kr_day_capsule_evaluation(first_request)
    second_evaluation = _advance(first_evaluation)
    second_request = _with_admission_features(_request_for(second_evaluation))
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    first_decision = run_kr_day_decision_tick((first_request,), decisions)[0]
    shadows = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    first = run_kr_day_capsule_shadow_tick(shadows, (first_evaluation,), decisions).results[0]
    _ = run_kr_day_decision_tick((second_request,), decisions)

    replay = run_kr_day_capsule_shadow_tick(shadows, (first_evaluation,), decisions).results[0]

    assert replay.created is False
    assert replay.event.event_id == first.event.event_id
    assert replay.decision_event_id == first_decision.event_id
    assert replay.decision_reason_codes == first.decision_reason_codes
    assert replay.market_gate_reasons == first.market_gate_reasons


@pytest.mark.parametrize("decision_kind", ("active", "pending", "nonarmed"))
def test_bound_decision_replay_fails_closed_when_original_store_is_missing(
    decision_kind: Literal["active", "pending", "nonarmed"],
    tmp_path: Path,
) -> None:
    match decision_kind:
        case "active":
            request = _with_admission_features(_request_for(_entry_evaluation()))
        case "pending":
            request = _admitted_pullback_request()
        case "nonarmed":
            request = _request_for(_entry_evaluation())
        case unreachable:
            assert_never(unreachable)
    evaluation = adapt_kr_day_capsule_evaluation(request)
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    original = run_kr_day_decision_tick((request,), decisions)[0]
    shadows = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    first = run_kr_day_capsule_shadow_tick(shadows, (evaluation,), decisions).results[0]

    with pytest.raises(InvalidKrDayCapsuleShadowServiceError):
        _ = run_kr_day_capsule_shadow_tick(
            shadows,
            (evaluation,),
            KrDayDecisionStore(tmp_path / "empty-decisions.sqlite3"),
        )

    assert first.decision_event_id == original.event_id
    assert len(shadows.events()) == 1


def test_decision_missing_replay_is_deterministic_without_fabricated_provenance(
    tmp_path: Path,
) -> None:
    evaluation = _entry_evaluation()
    decisions = KrDayDecisionStore(tmp_path / "empty-decisions.sqlite3")
    shadows = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    first = run_kr_day_capsule_shadow_tick(shadows, (evaluation,), decisions).results[0]
    changed_decisions = KrDayDecisionStore(tmp_path / "changed-decisions.sqlite3")
    _ = run_kr_day_decision_tick((_request_for(evaluation),), changed_decisions)

    replay = run_kr_day_capsule_shadow_tick(shadows, (evaluation,), changed_decisions).results[0]

    assert first.event.reason is KrDayCapsuleShadowReason.DECISION_MISSING
    assert replay.event.event_id == first.event.event_id
    assert replay.decision_event_id is None
    assert replay.decision_reason_codes == first.decision_reason_codes == ()
    assert replay.market_gate_reasons == first.market_gate_reasons


def test_active_management_replay_does_not_require_entry_decision(
    tmp_path: Path,
) -> None:
    request = _with_admission_features(_request_for(_entry_evaluation()))
    entry = adapt_kr_day_capsule_evaluation(request)
    collision = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    decisions = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    _ = run_kr_day_decision_tick((request,), decisions)
    shadows = KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3")
    _ = run_kr_day_capsule_shadow_tick(shadows, (entry,), decisions)
    terminal = run_kr_day_capsule_shadow_tick(
        shadows,
        (collision,),
        KrDayDecisionStore(tmp_path / "empty-decisions.sqlite3"),
    ).results[0]

    replay = run_kr_day_capsule_shadow_tick(
        shadows,
        (collision,),
        KrDayDecisionStore(tmp_path / "empty-decisions.sqlite3"),
    ).results[0]

    assert terminal.event.status is KrDayCapsuleShadowStatus.STOPPED
    assert replay.created is False
    assert replay.event.event_id == terminal.event.event_id
    assert replay.decision_event_id is None


@pytest.mark.parametrize(
    ("constraint", "expected"),
    (
        ("stale", KrIntradayGateReason.STALE_EVIDENCE),
        ("missing", KrIntradayGateReason.QUOTE_MISSING),
        ("crossed", KrIntradayGateReason.CROSSED_QUOTE),
        ("halted", KrIntradayGateReason.HALTED),
        ("vi", KrIntradayGateReason.VI_ACTIVE),
        ("call", KrIntradayGateReason.CALL_AUCTION),
        ("upper", KrIntradayGateReason.UPPER_LIMIT),
        ("lower", KrIntradayGateReason.LOWER_LIMIT),
        ("near", KrIntradayGateReason.NEAR_UPPER_LIMIT),
    ),
)
def test_market_constraint_reason_survives_without_fill(
    constraint: Literal["stale", "missing", "crossed", "halted", "vi", "call", "upper", "lower", "near"],
    expected: KrIntradayGateReason,
    tmp_path: Path,
) -> None:
    evaluation = _entry_evaluation()
    market = evaluation.market
    match constraint:
        case "stale":
            observed_at = evaluation.evaluated_at - dt.timedelta(seconds=6)
            market = market.model_copy(
                update={
                    "observed_at": observed_at,
                    "evidence_refs": tuple(
                        item.model_copy(update={"observed_at": observed_at})
                        for item in market.evidence_refs
                    ),
                }
            )
        case "missing":
            market = market.model_copy(update={"bid_price": None})
        case "crossed":
            market = market.model_copy(update={"bid_price": Decimal("10170")})
        case "halted":
            market = market.model_copy(update={"halt_state": KrHaltState.HALTED})
        case "vi":
            market = market.model_copy(update={"vi_state": KrViState.DYNAMIC_ACTIVE})
        case "call":
            market = market.model_copy(update={"trading_mode": KrTradingMode.CALL_AUCTION})
        case "upper":
            market = market.model_copy(update={"last_price": market.upper_limit_price})
        case "lower":
            market = market.model_copy(update={"last_price": market.lower_limit_price})
        case "near":
            market = market.model_copy(update={"last_price": Decimal("12700")})
        case unreachable:
            assert_never(unreachable)
    constrained = _rebuild(evaluation, market=market)

    result = run_authorized_kr_shadow_tick(
        KrDayCapsuleShadowStore(tmp_path / "shadow.sqlite3"),
        (constrained,),
    ).results[0]

    assert result.event.status is KrDayCapsuleShadowStatus.REGISTERED
    assert result.event.entry_price is None
    assert expected in result.market_gate_reasons
