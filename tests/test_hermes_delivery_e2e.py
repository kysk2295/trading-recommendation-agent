from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_contract_outbox import OBSERVED_AT, _opportunity, _publication
from tests.test_day_learning_report_models import _payload as _close_payload
from tests.test_day_learning_report_models import _report as _close_report
from tests.test_kr_day_decision_store import _event as _decision_event
from tests.test_kr_day_decision_store import _event_from_payload as _decision_from_payload
from tests.test_kr_day_decision_store import _payload as _decision_payload
from tests.test_kr_day_decision_store import _plan as _decision_plan
from trading_agent.contract_outbox import append_opportunity_snapshot, append_trade_signal_publication
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionSources,
    project_contract_outboxes,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_decision_delivery import (
    InvalidKrDayDecisionDeliveryError,
    KrDayDecisionDeliveryBatch,
    KrDayDeliveryIncident,
    project_kr_day_decision_delivery,
)
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.research_identity_models import MarketId


def test_contract_projection_preserves_watch_to_signal_reply_lineage(tmp_path: Path) -> None:
    # Given
    sources = HermesProjectionSources(
        opportunity_outbox=tmp_path / "opportunities.v1.jsonl",
        signal_outbox=tmp_path / "trade-signals.v1.jsonl",
    )
    opportunity = _opportunity()
    publication = _publication(signal_id="signal-1")
    assert append_opportunity_snapshot(sources.opportunity_outbox, opportunity) is True
    assert append_trade_signal_publication(sources.signal_outbox, tmp_path / "cards", publication) is True
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When
    with store.writer() as writer:
        first = project_contract_outboxes(sources, writer)
        replay = project_contract_outboxes(sources, writer)
        root_claim = writer.claim_next(worker_id="worker-a", now=OBSERVED_AT, lease_seconds=30)
        assert root_claim is not None
        _ = writer.acknowledge(root_claim, platform_message_id="telegram-100", acknowledged_at=OBSERVED_AT)
        reply_claim = writer.claim_next(
            worker_id="worker-a",
            now=OBSERVED_AT + dt.timedelta(seconds=5),
            lease_seconds=30,
        )

    # Then
    assert first.inserted == 2
    assert replay.inserted == 0
    assert reply_claim is not None
    assert reply_claim.event.agent_family == "day_trading"
    assert reply_claim.lineage.root_delivery_id == root_claim.event.delivery_id
    assert reply_claim.lineage.root_platform_message_id == "telegram-100"


def test_projection_covers_terminal_research_and_summary_delivery_kinds(tmp_path: Path) -> None:
    # Given
    kinds = (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.INVALIDATION,
        HermesDeliveryKind.EXIT,
        HermesDeliveryKind.INCIDENT,
        HermesDeliveryKind.NO_RECOMMENDATION,
        HermesDeliveryKind.RESEARCH,
        HermesDeliveryKind.DAILY_SUMMARY,
    )
    records = tuple(_outcome(kind, index) for index, kind in enumerate(kinds, start=1))
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When
    with store.writer() as writer:
        result = project_outcomes(records, writer)

    # Then
    assert result.inserted == len(kinds)
    assert tuple(event.kind for event in store.events()) == kinds


def test_kr_decision_history_projects_one_stable_actionable_thread(tmp_path: Path) -> None:
    # Given: one immutable ARMED decision and its bound shadow entry and exit history.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
    )
    active = _shadow_event(armed.event_id, KrDayCapsuleShadowStatus.ACTIVE)
    stopped = _shadow_event(
        armed.event_id,
        KrDayCapsuleShadowStatus.STOPPED,
        previous_event_id=active.event_id,
        occurred_at=active.occurred_at + dt.timedelta(minutes=1),
    )
    batch = KrDayDecisionDeliveryBatch(
        decision_events=(armed,),
        shadow_events=(active, stopped),
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: the complete history is projected twice, including a process-style restart.
    with store.writer() as writer:
        first = project_kr_day_decision_delivery(batch, writer)
    with HermesDeliveryStore(store.path).writer() as writer:
        replay = project_kr_day_decision_delivery(batch, writer)

    # Then: exactly ARMED, ACTIVE, and EXIT are retained in one stable thread.
    events = store.events()
    assert (first.inserted, replay.inserted) == (3, 0)
    assert tuple(event.kind for event in events) == (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.EXIT,
    )
    assert events[1].root_delivery_id == events[0].delivery_id
    assert events[2].root_delivery_id == events[0].delivery_id
    root_text = events[0].rendered_text
    for required in (
        "조건부",
        "shadow 전용",
        armed.observed_at.isoformat(),
        "Close above the completed-bar resistance",
        "71000",
        "70000",
        "72500",
        "Cancel if a completed bar closes below the stop",
        armed.valid_until.isoformat(),
        "Confirmed completed-bar setup",
        "bar://005930/2026-08-24T01:02Z",
    ):
        assert required in root_text
    assert "shadow 체결" in events[1].rendered_text
    assert "종료" in events[2].rendered_text
    assert events[2].evidence_refs == tuple(sorted((
        f"decision:{armed.event_id}",
        f"shadow:{active.event_id}",
        f"shadow:{stopped.event_id}",
    )))


def test_kr_repeated_no_signal_and_pending_histories_project_nothing(tmp_path: Path) -> None:
    # Given: ten independent no-signal investigations with REGISTERED pending shadows.
    investigating = _decision_event(status=KrDayDecisionStatus.INVESTIGATING)
    registered = _shadow_event(
        None,
        KrDayCapsuleShadowStatus.REGISTERED,
        reason_override=KrDayCapsuleShadowReason.DECISION_MISSING,
    )
    batch = KrDayDecisionDeliveryBatch(
        decision_events=(investigating,),
        shadow_events=(registered,),
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: the full no-signal history is projected.
    with store.writer() as writer:
        results = tuple(project_kr_day_decision_delivery(batch, writer) for _tick in range(10))

    # Then: internal research ticks remain silent.
    assert all((result.examined, result.inserted) == (0, 0) for result in results)
    assert store.events() == ()


def test_kr_projection_rejects_cross_capsule_shadow_binding(tmp_path: Path) -> None:
    # Given: a shadow event that claims an ARMED decision from another capsule.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
    )
    original = _shadow_event(armed.event_id, KrDayCapsuleShadowStatus.ACTIVE)
    payload = KrDayCapsuleShadowEventPayload.model_validate(
        original.model_dump(mode="python", exclude={"event_id"}) | {"capsule_id": "f" * 64}
    )
    active = KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When/Then: projection fails closed before inserting or threading it.
    with store.writer() as writer, pytest.raises(InvalidKrDayDecisionDeliveryError):
        _ = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch(decision_events=(armed,), shadow_events=(active,)),
            writer,
        )
    assert store.events() == ()


def test_kr_later_ready_armed_binding_replies_to_first_visible_armed(tmp_path: Path) -> None:
    # Given: a pending visible plan followed by a ready revision and its exact shadow lifecycle.
    first = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
    )
    completed = first.completed_bar_at + dt.timedelta(minutes=1)
    valid_until = completed + dt.timedelta(minutes=8)
    ready_payload = _decision_payload(
        status=KrDayDecisionStatus.ARMED,
        plan=_decision_plan(valid_until=valid_until),
        reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
        previous_event_id=first.event_id,
        completed_bar_at=completed,
        observed_at=completed + dt.timedelta(seconds=2),
        valid_until=valid_until,
    )
    ready = _decision_from_payload(
        ready_payload,
        KrDayDecisionEvent.canonical_id_for(ready_payload),
    )
    pending = _shadow_event(
        first.event_id,
        KrDayCapsuleShadowStatus.REGISTERED,
        occurred_at=first.observed_at,
    )
    active = _shadow_event(
        ready.event_id,
        KrDayCapsuleShadowStatus.ACTIVE,
        previous_event_id=pending.event_id,
        occurred_at=ready.observed_at,
    )
    targeted = _shadow_event(
        ready.event_id,
        KrDayCapsuleShadowStatus.TARGETED,
        previous_event_id=active.event_id,
        occurred_at=active.occurred_at + dt.timedelta(minutes=1),
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: the normal pending-to-ready full history is projected.
    with store.writer() as writer:
        result = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch(
                decision_events=(first, ready),
                shadow_events=(pending, active, targeted),
            ),
            writer,
        )

    # Then: the first ARMED is the root and replies preserve both decision revisions.
    events = store.events()
    assert result.inserted == 3
    assert all(event.root_delivery_id == events[0].delivery_id for event in events[1:])
    assert f"decision:{first.event_id}" in events[1].evidence_refs
    assert f"decision:{ready.event_id}" in events[1].evidence_refs


def test_kr_visible_plan_invalidation_and_standalone_supplements_map_once(tmp_path: Path) -> None:
    # Given: a visible ARMED thesis, later rejection, one service incident, and one KR close report.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
    )
    rejected = _decision_event(
        status=KrDayDecisionStatus.REJECTED,
        reason_codes=(KrDayDecisionReasonCode.SPREAD_TOO_WIDE,),
        previous_event_id=armed.event_id,
    )
    incident = KrDayDeliveryIncident(
        incident_id="9" * 64,
        occurred_at=armed.observed_at,
        scope="KR completed-bar feed",
        reason_codes=("stale_evidence",),
        evidence_refs=("cycle:broken-1",),
        capsule_id=armed.capsule_id,
        symbol=armed.symbol,
    )
    report = _close_report(_close_payload(MarketId.KR_EQUITIES))
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: state changes and standalone operational outputs are projected twice.
    batch = KrDayDecisionDeliveryBatch(
        decision_events=(armed, rejected),
        shadow_events=(),
        incidents=(incident,),
        close_reports=(report,),
    )
    with store.writer() as writer:
        first = project_kr_day_decision_delivery(batch, writer)
        replay = project_kr_day_decision_delivery(batch, writer)

    # Then: invalidation replies to ARMED while incident and summary remain standalone and deduplicated.
    events = store.events()
    assert (first.inserted, replay.inserted) == (4, 0)
    assert tuple(event.kind for event in events) == (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.INVALIDATION,
        HermesDeliveryKind.INCIDENT,
        HermesDeliveryKind.DAILY_SUMMARY,
    )
    assert events[1].root_delivery_id == events[0].delivery_id
    assert "SPREAD_TOO_WIDE" in events[1].rendered_text
    assert "실패 사유" in events[2].rendered_text
    assert "시도/지지/반박/불확정: 3/1/1/1" in events[3].rendered_text
    assert "challenger 결정 active/queued: 1/1" in events[3].rendered_text


def test_kr_registered_nonarmed_binding_remains_silent(tmp_path: Path) -> None:
    # Given: Task 6 retained a REGISTERED event bound to an exact rejected decision.
    rejected = _decision_event(
        status=KrDayDecisionStatus.REJECTED,
        reason_codes=(KrDayDecisionReasonCode.SPREAD_TOO_WIDE,),
    )
    registered = _shadow_event(
        rejected.event_id,
        KrDayCapsuleShadowStatus.REGISTERED,
        occurred_at=rejected.observed_at,
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: its complete internal history is projected.
    with store.writer() as writer:
        result = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch((rejected,), (registered,)),
            writer,
        )

    # Then: exact non-visible lineage validates but produces no user push.
    assert (result.examined, result.inserted, store.events()) == (0, 0, ())


def test_kr_legacy_unbound_active_is_not_announced(tmp_path: Path) -> None:
    # Given: one visible ARMED root and a pre-binding legacy ACTIVE shadow event.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
    )
    legacy = _shadow_event(None, KrDayCapsuleShadowStatus.ACTIVE, raw_signal_id="legacy-shadow-signal")
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: the history is projected through the current fail-closed delivery boundary.
    with store.writer() as writer:
        result = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch((armed,), (legacy,)),
            writer,
        )

    # Then: ARMED remains visible but unbound ACTIVE cannot become a reply.
    assert result.inserted == 1
    assert tuple(event.kind for event in store.events()) == (HermesDeliveryKind.ACTIONABLE,)


def test_kr_preentry_censored_plan_projects_truthful_no_fill_exit(tmp_path: Path) -> None:
    # Given: an ARMED plan was REGISTERED but censored before any shadow fill.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
    )
    registered = _shadow_event(
        armed.event_id,
        KrDayCapsuleShadowStatus.REGISTERED,
        occurred_at=armed.observed_at,
    )
    censored = _shadow_event(
        armed.event_id,
        KrDayCapsuleShadowStatus.CENSORED,
        previous_event_id=registered.event_id,
        occurred_at=registered.occurred_at + dt.timedelta(minutes=1),
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When: the complete no-fill lifecycle is projected.
    with store.writer() as writer:
        result = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch((armed,), (registered, censored)),
            writer,
        )

    # Then: one EXIT reply closes the visible plan without inventing a fill or position prices.
    events = store.events()
    assert result.inserted == 2
    assert tuple(event.kind for event in events) == (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.EXIT,
    )
    assert events[1].root_delivery_id == events[0].delivery_id
    assert "미체결" in events[1].rendered_text
    assert "계획 종료" in events[1].rendered_text
    assert "체결가" not in events[1].rendered_text
    assert events[1].evidence_refs == tuple(
        sorted(
            (
                f"decision:{armed.event_id}",
                f"shadow:{registered.event_id}",
                f"shadow:{censored.event_id}",
            )
        )
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        (KrDayDecisionStatus.REJECTED, KrDayDecisionReasonCode.SPREAD_TOO_WIDE),
        (KrDayDecisionStatus.BLOCKED, KrDayDecisionReasonCode.MARKET_GATE_BLOCKED),
    ),
)
def test_kr_invalidation_or_blocked_plus_bound_active_rejects_before_insert(
    tmp_path: Path,
    status: KrDayDecisionStatus,
    reason: KrDayDecisionReasonCode,
) -> None:
    # Given: one thesis contains mutually exclusive pre-entry invalidation and bound ACTIVE facts.
    armed = _decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
    )
    invalidation = _decision_event(
        status=status,
        reason_codes=(reason,),
        previous_event_id=armed.event_id,
    )
    active = _shadow_event(
        armed.event_id,
        KrDayCapsuleShadowStatus.ACTIVE,
        occurred_at=invalidation.observed_at + dt.timedelta(seconds=1),
    )
    store = HermesDeliveryStore(tmp_path / "kr-delivery.sqlite3")

    # When/Then: semantic validation fails before the ARMED root can be inserted.
    with store.writer() as writer, pytest.raises(InvalidKrDayDecisionDeliveryError):
        _ = project_kr_day_decision_delivery(
            KrDayDecisionDeliveryBatch((armed, invalidation), (active,)),
            writer,
        )
    assert store.events() == ()


def _outcome(kind: HermesDeliveryKind, index: int) -> HermesProjectionRecord:
    return HermesProjectionRecord(
        source_event_id=f"outcome-{index}",
        root_source_event_id=None,
        kind=kind,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id="intraday_momentum",
        strategy_version="orb-v1",
        instrument_id="ACME",
        occurred_at=OBSERVED_AT + dt.timedelta(seconds=index),
        status=kind.value,
        evidence_refs=(f"terminal:event-{index}",),
        rendered_text=f"{kind.value} outcome",
        payload_sha256=f"{index:x}" * 64,
    )


def _shadow_event(
    decision_event_id: str | None,
    status: KrDayCapsuleShadowStatus,
    *,
    previous_event_id: str | None = None,
    occurred_at: dt.datetime = OBSERVED_AT,
    raw_signal_id: str | None = None,
    reason_override: KrDayCapsuleShadowReason | None = None,
) -> KrDayCapsuleShadowEvent:
    match status:
        case KrDayCapsuleShadowStatus.REGISTERED:
            reason = KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING
        case KrDayCapsuleShadowStatus.ACTIVE:
            reason = KrDayCapsuleShadowReason.ENTRY
        case KrDayCapsuleShadowStatus.STOPPED:
            reason = KrDayCapsuleShadowReason.STOP_FIRST
        case KrDayCapsuleShadowStatus.TARGETED:
            reason = KrDayCapsuleShadowReason.TARGET
        case KrDayCapsuleShadowStatus.CENSORED:
            reason = KrDayCapsuleShadowReason.BAR_GAP
        case _:
            raise AssertionError
    if reason_override is not None:
        reason = reason_override
    has_position = status in {
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowStatus.TARGETED,
    }
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id="a" * 64,
        evaluation_id=("d" if previous_event_id is None else "e") * 64,
        session_date=dt.date(2026, 8, 24),
        calendar_snapshot_id="calendar-1",
        collection_cycle_id="cycle-1",
        symbol="005930",
        attempted_bar_cursor=occurred_at,
        accepted_bar_cursor=None if status is KrDayCapsuleShadowStatus.CENSORED else occurred_at,
        previous_event_id=previous_event_id,
        status=status,
        reason=reason,
        signal_id=(
            raw_signal_id
            if raw_signal_id is not None
            else None
            if decision_event_id is None
            else f"kr-day-decision-{decision_event_id}"
        ),
        entry_price=Decimal("71100") if has_position else None,
        stop_price=Decimal("70000") if has_position else None,
        target_prices=(Decimal("72500"), Decimal("74000")) if has_position else (),
        occurred_at=occurred_at,
        evaluation_payload_sha256="1" * 64,
        bar_payload_sha256="2" * 64,
    )
    return KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )
