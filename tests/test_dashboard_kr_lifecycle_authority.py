from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_dashboard_projection_day_agent import _decision, _lifecycle_items, _rendered, _shadow
from trading_agent.dashboard_projection_day_agent import project_day_agent_facade
from trading_agent.dashboard_projection_day_agent_kr import project_kr_day_lifecycle
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore


def test_snapshot_reads_explicit_operational_kr_state_root(tmp_path: Path) -> None:
    # Given: outputs contain no KR ledgers while the explicit service state root does.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    state_root = tmp_path / "kr-service-state"
    decision = _decision(KrDayDecisionStatus.INVESTIGATING)
    assert KrDayDecisionStore(state_root / "kr-day-decisions.sqlite3").append(decision)

    # When: the public snapshot API receives the operational root.
    snapshot = collect_dashboard_snapshot_v2(
        outputs,
        now=decision.observed_at + dt.timedelta(seconds=1),
        kr_day_state_root=state_root,
    )

    # Then: the real lifecycle is present without copying it beneath outputs.
    lifecycle = _lifecycle_items(snapshot.workspaces.markets.items)
    assert any("INVESTIGATING" in (item.value or "") for item in lifecycle)
    assert not (outputs / "kr_day").exists()


@pytest.mark.parametrize(
    ("kind", "canonical", "duplicate"),
    [
        ("decision", "kr-day-decisions.sqlite3", "decisions.sqlite3"),
        ("shadow", "kr-day-capsule-shadow.sqlite3", "shadow.sqlite3"),
    ],
)
def test_multiple_recognized_ledgers_fail_closed(
    kind: str,
    canonical: str,
    duplicate: str,
    tmp_path: Path,
) -> None:
    # Given: two individually valid recognized candidates for one ledger kind.
    root = tmp_path / "state"
    decision = _decision(KrDayDecisionStatus.INVESTIGATING)
    if kind == "decision":
        assert KrDayDecisionStore(root / canonical).append(decision)
        assert KrDayDecisionStore(root / duplicate).append(decision)
    else:
        shadow = _shadow(KrDayCapsuleShadowStatus.BLOCKED, decision.event_id, decision.observed_at)
        assert KrDayCapsuleShadowStore(root / canonical).append(shadow)
        assert KrDayCapsuleShadowStore(root / duplicate).append(shadow)

    # When: the lifecycle selects its immutable source.
    projection = project_kr_day_lifecycle(root, now=decision.observed_at)

    # Then: ambiguity is source corruption, never first-match selection.
    assert projection.items
    assert all(item.state == "corrupt" for item in projection.items)
    assert f"{kind} ledger corrupt" in _rendered(projection.items)


def test_corrupt_thesis_history_does_not_hide_valid_sibling(tmp_path: Path) -> None:
    # Given: one valid thesis and a distinct thesis with contradictory ACTIVE/BLOCKED history.
    root = tmp_path / "state"
    bad_armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    bad_blocked = _decision(
        KrDayDecisionStatus.BLOCKED,
        previous_event_id=bad_armed.event_id,
        reasons=(KrDayDecisionReasonCode.MARKET_GATE_BLOCKED,),
    )
    good = _with_identity(_decision(KrDayDecisionStatus.INVESTIGATING), capsule_id="d" * 64, opportunity_id="good")
    store = KrDayDecisionStore(root / "kr-day-decisions.sqlite3")
    assert store.append(bad_armed) and store.append(bad_blocked) and store.append(good)
    active = _shadow(
        KrDayCapsuleShadowStatus.ACTIVE,
        bad_armed.event_id,
        bad_armed.observed_at + dt.timedelta(minutes=1),
    )
    assert KrDayCapsuleShadowStore(root / "kr-day-capsule-shadow.sqlite3").append(active)

    # When: both thesis histories are projected.
    projection = project_kr_day_lifecycle(root, now=active.occurred_at + dt.timedelta(seconds=1))

    # Then: the valid sibling remains visible beside one thesis-local corrupt card.
    recommendations = tuple(item for item in projection.items if item.kind == "day_recommendation")
    corrupt = tuple(item for item in projection.items if item.state == "corrupt")
    assert len(recommendations) == 1
    assert "INVESTIGATING" in (recommendations[0].value or "")
    assert len(corrupt) == 1
    assert "decision/shadow binding corrupt" in (corrupt[0].value or "")


def test_hidden_fourth_thesis_shadows_are_classified_before_display_cap(tmp_path: Path) -> None:
    # Given: four theses, with only the oldest carrying a valid registration and malformed follow-up.
    root = tmp_path / "state"
    first_bar = dt.datetime(2026, 8, 24, 1, 2, tzinfo=dt.UTC)
    decisions = tuple(
        _with_identity(
            _decision(
                KrDayDecisionStatus.ARMED,
                completed_bar_at=first_bar + dt.timedelta(minutes=index),
                reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
            ),
            capsule_id=f"{index + 1}" * 64,
            opportunity_id=f"thesis-{index}",
        )
        for index in range(4)
    )
    decision_store = KrDayDecisionStore(root / "kr-day-decisions.sqlite3")
    assert all(decision_store.append(decision) for decision in decisions)
    hidden = decisions[0]
    registered = _shadow(
        KrDayCapsuleShadowStatus.REGISTERED,
        hidden.event_id,
        hidden.observed_at + dt.timedelta(minutes=1),
        capsule_id=hidden.capsule_id,
    )
    malformed = _shadow(
        KrDayCapsuleShadowStatus.ACTIVE,
        hidden.event_id,
        registered.occurred_at + dt.timedelta(minutes=1),
        previous_event_id=registered.event_id,
        capsule_id="f" * 64,
    )
    shadow_store = KrDayCapsuleShadowStore(root / "kr-day-capsule-shadow.sqlite3")
    assert shadow_store.append(registered) and shadow_store.append(malformed)

    # When: the dashboard limits its recommendation display to the three latest thesis groups.
    projection = project_kr_day_lifecycle(root, now=malformed.occurred_at + dt.timedelta(seconds=1))

    # Then: all hidden shadows are classified, while none can borrow into a visible thesis card.
    recommendations = tuple(item for item in projection.items if item.kind == "day_recommendation")
    rendered = _rendered(projection.items)
    assert len(recommendations) == 3
    assert "day_agent.kr.lifecycle.unbound" not in {item.item_id for item in projection.items}
    assert "ACTIVE" not in rendered and "71100" not in rendered


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (KrDayCapsuleShadowStatus.BLOCKED, "signal_blocked"),
        (KrDayCapsuleShadowStatus.FAILED, "invalid_evaluation"),
    ],
)
def test_bound_non_actionable_shadow_never_renders_armed_plan(
    status: KrDayCapsuleShadowStatus,
    reason: str,
    tmp_path: Path,
) -> None:
    # Given: an ARMED decision followed by a bound non-actionable shadow terminal.
    root = tmp_path / "state"
    armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    terminal = _shadow(status, armed.event_id, armed.observed_at + dt.timedelta(minutes=1))
    assert KrDayDecisionStore(root / "kr-day-decisions.sqlite3").append(armed)
    assert KrDayCapsuleShadowStore(root / "kr-day-capsule-shadow.sqlite3").append(terminal)

    # When: the bound lifecycle is rendered.
    projection = project_kr_day_lifecycle(root, now=terminal.occurred_at + dt.timedelta(seconds=1))

    # Then: its exact terminal truth is visible with no actionable card or prices.
    rendered = _rendered(projection.items)
    assert status.value.upper() in rendered and reason in rendered
    assert "evaluation evidence " + "1" * 64 in rendered
    assert "bar evidence " + "2" * 64 in rendered
    assert terminal.event_id in rendered
    assert all(item.kind != "day_recommendation" for item in projection.items)
    assert all(token not in rendered for token in ("entry 71000", "stop 70000", "targets 72500/74000"))


def test_active_uses_exact_bound_armed_plan_and_preserves_root_identity(tmp_path: Path) -> None:
    # Given: an ARMED decision, a later different ARMED plan, and ACTIVE bound to the first.
    root = tmp_path / "state"
    first = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    store = KrDayDecisionStore(root / "kr-day-decisions.sqlite3")
    assert store.append(first)
    initial = project_kr_day_lifecycle(root, now=first.observed_at)
    initial_card = next(item for item in initial.items if item.kind == "day_recommendation")
    later = _later_armed(first)
    assert store.append(later)
    active = _shadow(KrDayCapsuleShadowStatus.ACTIVE, first.event_id, later.observed_at + dt.timedelta(minutes=1))
    assert KrDayCapsuleShadowStore(root / "kr-day-capsule-shadow.sqlite3").append(active)

    # When: the latest visible lifecycle is projected.
    projection = project_day_agent_facade(tmp_path / "outputs", now=active.occurred_at, kr_day_state_root=root)

    # Then: identity stays rooted and every user-visible plan field comes from the bound first ARMED event.
    items = _lifecycle_items(projection.markets)
    card = next(item for item in items if item.kind == "day_recommendation")
    rendered = _rendered(items)
    assert card.item_id == initial_card.item_id and card.trace_id == initial_card.trace_id
    assert all(
        token in rendered
        for token in (
            "ACTIVE",
            "fill 71100",
            "fill time",
            "stop 70000",
            "targets 72500/74000",
            "Confirmed completed-bar setup",
            "Cancel if a completed bar closes below the stop",
            "valid 2026-08-24T01:10:00+00:00",
            "unrealized unavailable",
            "SHADOW/PAPER ONLY",
        )
    )
    assert "later adversarial plan" not in rendered


def _with_identity(event: KrDayDecisionEvent, *, capsule_id: str, opportunity_id: str) -> KrDayDecisionEvent:
    values = event.model_dump(mode="python", exclude={"event_id"})
    if event.conditional_plan is not None:
        values["conditional_plan"] = event.conditional_plan.model_copy(update={"capsule_id": capsule_id})
    payload = KrDayDecisionEventPayload.model_validate(
        values | {"capsule_id": capsule_id, "opportunity_id": opportunity_id}
    )
    return KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python") | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )


def _later_armed(first: KrDayDecisionEvent) -> KrDayDecisionEvent:
    assert first.conditional_plan is not None
    completed = first.completed_bar_at + dt.timedelta(minutes=1)
    valid_until = completed + dt.timedelta(minutes=8)
    plan = first.conditional_plan.model_copy(
        update={
            "trigger_price": Decimal("81000"),
            "stop_price": Decimal("80000"),
            "target_prices": (Decimal("82500"), Decimal("84000")),
            "trigger_rule": "later trigger",
            "invalidation_rule": "later invalidation",
            "rationale": "later adversarial plan",
            "valid_until": valid_until,
        }
    )
    payload = KrDayDecisionEventPayload.model_validate(
        first.model_dump(mode="python", exclude={"event_id"})
        | {
            "completed_bar_at": completed,
            "observed_at": completed + dt.timedelta(seconds=2),
            "valid_until": valid_until,
            "conditional_plan": plan,
            "previous_event_id": first.event_id,
        }
    )
    return KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python") | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )
