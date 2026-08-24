from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.day_strategy_capsule_support import builtin_request
from tests.test_day_session_service import _config
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation, _plain_evaluation
from tests.test_kr_day_capsule_shadow_cli import _publish_request, _request_for
from trading_agent.day_session_service import run_day_session_service_tick
from trading_agent.day_session_service_config import KrDaySessionServiceConfig
from trading_agent.day_strategy_capsule import build_strategy_capsule
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import KrDayDecisionReasonCode, KrDayDecisionStatus
from trading_agent.kr_day_decision_service import run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import FeatureValue

_FIRST_CYCLE_STATUSES = frozenset({"INVESTIGATING", "ARMED", "REJECTED", "BLOCKED", "EXPIRED"})
_RESOLVED_STATUSES = frozenset({"ARMED", "REJECTED", "BLOCKED", "EXPIRED"})


def test_active_capsule_persists_one_reason_bearing_pre_entry_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one active KR capsule backed by a current-session completed-bar request.
    evaluation = _plain_evaluation()
    request = _publish_request(tmp_path, "first-cycle", _request_for(evaluation))
    config = _kr_config(tmp_path)
    _activate_capsule(monkeypatch, evaluation.capsule_id, request)

    # When: the public KR day-session service runs one real shadow child cycle.
    result = run_day_session_service_tick(
        config,
        clock=lambda: evaluation.evaluated_at.astimezone(dt.UTC),
    )

    # Then: the active capsule cannot disappear behind a None or an unreasoned shadow event.
    decisions = getattr(result, "decisions", ())
    assert result.status == "processed"
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision is not None
    assert decision.status in _FIRST_CYCLE_STATUSES
    assert decision.reason_codes
    assert decision.observed_at is not None
    assert len(KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3").events()) == 1


def test_unchanged_candidate_resolves_by_the_next_completed_bar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same active capsule on two consecutive completed bars.
    first = _plain_evaluation()
    second = _advance(first)
    first_request = _publish_request(tmp_path, "first-cycle", _request_for(first))
    second_request = _publish_request(tmp_path, "second-cycle", _request_for(second))
    config = _kr_config(tmp_path)
    _activate_capsule(monkeypatch, first.capsule_id, first_request, second_request)

    # When: the public service observes the candidate again on the next completed bar.
    _ = run_day_session_service_tick(config, clock=lambda: first.evaluated_at.astimezone(dt.UTC))
    result = run_day_session_service_tick(config, clock=lambda: second.evaluated_at.astimezone(dt.UTC))

    # Then: an unchanged candidate is no longer left indefinitely INVESTIGATING.
    decisions = getattr(result, "decisions", ())
    assert result.status == "processed"
    assert len(decisions) == 1
    assert decisions[0].status in _RESOLVED_STATUSES
    events = KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3").events()
    assert len(events) == 2
    assert events[1].previous_event_id == events[0].event_id


def test_canonical_signal_opportunity_id_survives_store_and_replay(tmp_path: Path) -> None:
    # Given: the actual canonical identifier shape emitted by the KR theme projection.
    request = _request_for(_plain_evaluation())
    opportunity_id = "kr-theme-opportunity-20260824T000000000000Z-0123456789ab"
    current = request.model_copy(
        update={"opportunity": request.opportunity.model_copy(update={"opportunity_id": opportunity_id})}
    )
    store = KrDayDecisionStore(tmp_path / "decisions.sqlite3")

    # When: the same completed-bar request is evaluated twice.
    first = run_kr_day_decision_tick((current,), store)
    replay = run_kr_day_decision_tick((current,), store)

    # Then: the source identifier is retained exactly and replay adds no row.
    assert first == replay
    assert first[0].opportunity_id == opportunity_id
    assert store.events() == first


def test_three_capsules_are_persisted_in_deterministic_order(tmp_path: Path) -> None:
    # Given: three distinct registered capsules evaluating the same completed bar.
    request = _request_for(_plain_evaluation())
    capsules = tuple(
        build_strategy_capsule(
            replace(
                builtin_request(market_id=MarketId.KR_EQUITIES),
                hypothesis_version_id=character * 64,
            )
        )
        for character in ("c", "d", "e")
    )
    requests = tuple(request.model_copy(update={"capsule": capsule}) for capsule in reversed(capsules))

    # When: the public decision service evaluates the batch.
    decisions = run_kr_day_decision_tick(requests, KrDayDecisionStore(tmp_path / "decisions.sqlite3"))

    # Then: every capsule produces one event in canonical capsule order.
    assert tuple(item.capsule_id for item in decisions) == tuple(sorted(item.capsule_id for item in decisions))
    assert len(decisions) == 3


def test_invalid_raw_request_does_not_hide_valid_sibling(tmp_path: Path) -> None:
    # Given: one canonical request beside one raw boundary object that cannot be revalidated.
    valid = _request_for(_plain_evaluation())
    invalid = KrDayCapsuleEvaluationRequest.model_construct()

    # When: the public decision batch revalidates both siblings.
    with pytest.raises(ValueError):
        run_kr_day_decision_tick((invalid, valid), KrDayDecisionStore(tmp_path / "decisions.sqlite3"))

    # Then: no identity-free disposition is fabricated.


def test_same_batch_armed_thesis_rejects_second_capsule(tmp_path: Path) -> None:
    # Given: two distinct capsules with the same admitted pullback thesis.
    request = _admitted_pullback_request()
    capsules = tuple(
        build_strategy_capsule(
            replace(builtin_request(market_id=MarketId.KR_EQUITIES), hypothesis_version_id=value * 64)
        )
        for value in ("c", "d")
    )
    batch = tuple(request.model_copy(update={"capsule": capsule}) for capsule in capsules)

    # When: both are evaluated in one decision-service call.
    decisions = run_kr_day_decision_tick(batch, KrDayDecisionStore(tmp_path / "decisions.sqlite3"))

    # Then: the first arms and the same-batch duplicate is explicitly rejected.
    assert tuple(item.status for item in decisions) == (KrDayDecisionStatus.ARMED, KrDayDecisionStatus.REJECTED)
    assert decisions[0].reason_codes == (KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,)
    assert decisions[1].reason_codes == (KrDayDecisionReasonCode.DUPLICATE_THESIS,)


def test_changed_same_bar_input_conflicts_with_replay(tmp_path: Path) -> None:
    # Given: one persisted decision and a changed quote under the same decision identity.
    request = _request_for(_plain_evaluation())
    store = KrDayDecisionStore(tmp_path / "decisions.sqlite3")
    first = run_kr_day_decision_tick((request,), store)[0]
    assert request.market.bid_price is not None
    changed_market = request.market.model_copy(update={"bid_price": request.market.bid_price - 1})
    changed = request.model_copy(update={"market": changed_market})

    # When/Then: replay succeeds only for the exact canonical input.
    assert run_kr_day_decision_tick((request,), store)[0].event_id == first.event_id
    with pytest.raises(ValueError):
        run_kr_day_decision_tick((changed,), store)
    evidence = {item.name: item.value for item in first.observed_evidence}
    assert len(evidence["decision_input_sha256"]) == 64


def test_expired_opportunity_before_completed_bar_uses_valid_deadline(tmp_path: Path) -> None:
    # Given: an opportunity that expired before the latest completed bar.
    request = _request_for(_plain_evaluation())
    completed = request.bars[-1].end_at
    opportunity = request.opportunity.model_copy(update={"valid_until": completed - dt.timedelta(seconds=30)})
    expired = request.model_copy(update={"opportunity": opportunity})

    # When: the expired candidate is audited.
    event = run_kr_day_decision_tick((expired,), KrDayDecisionStore(tmp_path / "decisions.sqlite3"))[0]

    # Then: its canonical deadline remains model-valid and truthful.
    assert event.status is KrDayDecisionStatus.EXPIRED
    assert event.valid_until == completed


def test_identifiable_invalid_input_is_persisted_and_sibling_isolated(tmp_path: Path) -> None:
    # Given: an identity-bearing request with a capsule policy contract mismatch beside a valid sibling.
    valid = _request_for(_plain_evaluation())
    other = build_strategy_capsule(
        replace(builtin_request(market_id=MarketId.KR_EQUITIES), hypothesis_version_id="f" * 64)
    )
    invalid_capsule = other.model_copy()
    object.__setattr__(invalid_capsule, "risk_policy_ref", "risk-policy://unsupported/v2")
    invalid = valid.model_copy()
    object.__setattr__(invalid, "capsule", invalid_capsule)

    # When: both raw inputs are processed together.
    events = run_kr_day_decision_tick((invalid, valid), KrDayDecisionStore(tmp_path / "decisions.sqlite3"))

    # Then: the invalid identity is audited without hiding its valid sibling.
    blocked = next(item for item in events if item.capsule_id == invalid_capsule.capsule_id)
    assert blocked.status is KrDayDecisionStatus.BLOCKED
    assert blocked.reason_codes == (KrDayDecisionReasonCode.POLICY_INPUT_CONTRACT_MISMATCH,)
    assert {item.name: item.value for item in blocked.observed_evidence}["input_valid"] == "false"
    assert len(events) == 2


def test_reclaim_uses_grid_normalized_quote_and_readiness_reason(tmp_path: Path) -> None:
    # Given: an admitted reclaim with a fresh off-grid ask.
    request = _with_admission_features(_request_for(_entry_evaluation()))
    market = request.market.model_copy(update={"ask_price": Decimal("10155.1")})
    current = request.model_copy(update={"market": market})

    # When: the reclaim is projected into a pre-entry decision.
    event = run_kr_day_decision_tick((current,), KrDayDecisionStore(tmp_path / "decisions.sqlite3"))[0]

    # Then: the trigger is normalized upward and no fill is claimed.
    assert event.status is KrDayDecisionStatus.ARMED
    assert event.reason_codes == (KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,)
    assert event.conditional_plan is not None
    assert event.conditional_plan.trigger_price == Decimal("10160")
    evidence = {item.name: item.value for item in event.observed_evidence}
    assert evidence["entry_trigger_ask"] == "10155.1"
    assert evidence["entry_trigger_normalized"] == "10160"


def test_active_shadow_management_survives_decision_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real ACTIVE shadow position and its next completed-bar request.
    first = _entry_evaluation()
    second = _advance(first)
    first_path = _publish_request(tmp_path, "active", _request_for(first))
    second_path = _publish_request(tmp_path, "manage", _request_for(second))
    config = _kr_config(tmp_path)
    _activate_capsule(monkeypatch, first.capsule_id, first_path, second_path)
    initial = run_day_session_service_tick(config, clock=lambda: first.evaluated_at.astimezone(dt.UTC))
    assert initial.status == "processed"
    monkeypatch.setattr(
        "trading_agent.day_session_service.run_kr_day_decision_tick",
        lambda *_args: (_ for _ in ()).throw(ValueError("forced decision failure")),
    )

    # When: decision processing fails on the management bar.
    result = run_day_session_service_tick(config, clock=lambda: second.evaluated_at.astimezone(dt.UTC))

    # Then: the real child still manages the active position without fabricating a decision.
    shadow = KrDayCapsuleShadowStore(config.state_root / "kr-day-capsule-shadow.sqlite3").events()
    assert result.status == "processed"
    assert result.reason == "shadow_managed_decision_blocked"
    assert result.decisions == ()
    assert shadow[-1].status is KrDayCapsuleShadowStatus.ACTIVE


def _admitted_pullback_request() -> KrDayCapsuleEvaluationRequest:
    request = _request_for(_entry_evaluation())
    bars = list(request.bars)
    bars[-1] = bars[-1].model_copy(
        update={
            "open": Decimal("10030"),
            "high": Decimal("10040"),
            "low": Decimal("10000"),
            "close": Decimal("10020"),
            "volume": 200,
            "trading_value_krw": Decimal("2004000"),
        }
    )
    request = _with_admission_features(request)
    market = request.market.model_copy(
        update={"last_price": Decimal("10025"), "bid_price": Decimal("10020"), "ask_price": Decimal("10030")}
    )
    return request.model_copy(update={"bars": tuple(bars), "market": market})


def _with_admission_features(request: KrDayCapsuleEvaluationRequest) -> KrDayCapsuleEvaluationRequest:
    features = {item.name: item.value for item in request.opportunity.candidates[0].features}
    features.update(
        theme_catalyst_count="2",
        theme_publisher_count="2",
        theme_related_symbol_count="3",
    )
    candidate = request.opportunity.candidates[0].model_copy(
        update={"features": tuple(FeatureValue(name=name, value=value) for name, value in sorted(features.items()))}
    )
    opportunity = request.opportunity.model_copy(update={"candidates": (candidate,)})
    return request.model_copy(update={"opportunity": opportunity})


def test_no_opportunity_cycle_does_not_fabricate_a_recommendation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an open KR service cycle without an active capsule or opportunity.
    config = _kr_config(tmp_path)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)

    # When: the public service runs its no-op cycle.
    result = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 10, 2, 2, tzinfo=dt.UTC),
    )

    # Then: no decision projection fabricates a recommendation.
    assert result.status == "no_action"
    assert getattr(result, "decisions", ()) == ()


def _kr_config(tmp_path: Path) -> KrDaySessionServiceConfig:
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    return config


def _activate_capsule(
    monkeypatch: pytest.MonkeyPatch,
    capsule_id: str,
    first_request: Path,
    second_request: Path | None = None,
) -> None:
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr(
        "trading_agent.day_session_service._kr_active_capsule_ids",
        lambda _ledger, _now: (capsule_id,),
    )
    requests = (first_request,) if second_request is None else (first_request, second_request)
    call_count = 0

    def materialize(
        _config: KrDaySessionServiceConfig,
        _now: dt.datetime,
        _capsules: tuple[str, ...],
    ) -> tuple[Path, ...]:
        nonlocal call_count
        request = requests[min(call_count, len(requests) - 1)]
        call_count += 1
        return (request,)

    monkeypatch.setattr("trading_agent.day_session_service._materialize_kr_requests", materialize)
