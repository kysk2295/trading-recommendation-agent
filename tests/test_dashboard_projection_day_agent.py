from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_day_learning_report_models import NOW, _payload, _report
from tests.test_kr_day_decision_store import HEX_A, _plan
from tests.test_kr_day_decision_store import _payload as _decision_payload
from tests.test_us_day_signal_admission import _eligible_request
from trading_agent.dashboard_models_v2 import WorkspaceItemV2
from trading_agent.dashboard_projection_day_agent import project_day_agent_facade
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_learning_report_store import publish_market_close_report
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_thesis_store import UsDayThesisStore


def test_projects_independent_us_paper_us_shadow_and_kr_read_only_lanes(tmp_path: Path) -> None:
    # Given: immutable close evidence for each market.
    outputs = tmp_path / "outputs"
    us_report = _report(_payload())
    kr_report = _report(_payload(MarketId.KR_EQUITIES))
    _publish(outputs / "us_day" / "close_reports", us_report)
    _publish(outputs / "kr_day" / "close_reports", kr_report)
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(_eligible_request().thesis)

    # When: the query-only dashboard facade reads the two isolated roots.
    projection = project_day_agent_facade(outputs, now=NOW + dt.timedelta(minutes=1))

    # Then: each lane is independently named, with no combined performance projection or writer authority.
    labels = {item.label for item in (*projection.markets, *projection.research)}
    assert "US · Alpaca Paper" in labels
    assert "US · Shadow" in labels
    assert "KR · Shadow · provider read-only" in labels
    rendered = " ".join((item.value or "") for item in (*projection.markets, *projection.research))
    assert "active" in rendered and "queued" in rendered and "suspended" in rendered
    assert all(token in rendered for token in ("entry", "stop", "targets", "rationale", "outcome"))
    assert "combined" not in rendered.lower()
    assert "confidence" not in rendered.lower()
    assert all("return" not in item.item_id for item in (*projection.markets, *projection.research))
    assert all("order" not in item.item_id for item in (*projection.markets, *projection.research))
    assert projection.daily_learning_report is not None
    assert all("return" not in field for field in type(projection.daily_learning_report).model_fields)


def test_corrupt_kr_evidence_does_not_hide_valid_us_lanes(tmp_path: Path) -> None:
    # Given: a verified US report and a corrupt KR source file.
    outputs = tmp_path / "outputs"
    _publish(outputs / "us_day" / "close_reports", _report(_payload()))
    corrupt = outputs / "kr_day" / "close_reports" / "market_close_report_invalid.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{", encoding="utf-8")
    corrupt.chmod(0o600)

    # When: the facade projects both independently.
    projection = project_day_agent_facade(outputs, now=NOW + dt.timedelta(minutes=1))

    # Then: US remains available while KR alone fails closed.
    us = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.us"))
    kr = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.kr"))
    assert us and all(item.state in {"populated", "empty"} for item in us)
    assert kr and all(item.state == "corrupt" for item in kr)
    assert kr[0].value == "KR evidence invalid"


def test_projects_latest_investigating_evidence_and_missing_confirmations(tmp_path: Path) -> None:
    # Given: two immutable decisions for one KR thesis, with current observed evidence.
    outputs = tmp_path / "outputs"
    first = _decision(KrDayDecisionStatus.INVESTIGATING)
    latest = _decision(
        KrDayDecisionStatus.INVESTIGATING,
        previous_event_id=first.event_id,
        completed_bar_at=first.completed_bar_at + dt.timedelta(minutes=1),
        reasons=(
            KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING,
            KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING,
        ),
        evidence=(KrDayDecisionEvidenceValue(name="volume_ratio", value="1.8"),),
    )
    decisions = KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3")
    assert decisions.append(first) and decisions.append(latest)

    # When: the dashboard reads the production service state contract.
    projection = project_day_agent_facade(outputs, now=latest.observed_at + dt.timedelta(seconds=9))

    # Then: one latest card exposes evidence, missing confirmations, immutable identity, and freshness.
    items = _lifecycle_items(projection.markets)
    assert len(tuple(item for item in items if item.kind == "day_recommendation")) == 1
    rendered = _rendered(items)
    assert all(value in rendered for value in ("INVESTIGATING", "volume_ratio=1.8", "CATALYST_SOURCE_MISSING"))
    assert all(value in rendered for value in ("SHADOW/PAPER ONLY", "KRX 2026-08-24T01:03:00+00:00"))
    assert "cap aaaaaaaa/hyp bbbbbbbb" in rendered
    assert "evidence age 9s" in rendered
    assert next(item for item in items if item.kind == "day_recommendation").observed_at == latest.observed_at


def test_projects_armed_conditional_plan_without_order_authority(tmp_path: Path) -> None:
    # Given: one immutable ARMED decision with its complete conditional plan.
    outputs = tmp_path / "outputs"
    armed = _decision(
        KrDayDecisionStatus.ARMED,
        reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
    )
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(armed)

    # When: the dashboard projects the current thesis.
    projection = project_day_agent_facade(outputs, now=armed.observed_at + dt.timedelta(seconds=3))

    # Then: price, condition, validity, and invalidation are visible with no real-order claim.
    rendered = _rendered(_lifecycle_items(projection.markets))
    assert all(value in rendered for value in ("ARMED", "entry 71000", "stop 70000", "targets 72500/74000"))
    assert all(value in rendered for value in ("Close above", "Cancel if", "valid 2026-08-24T01:10:00+00:00"))
    assert "SHADOW/PAPER ONLY" in rendered
    assert "order authority" not in rendered.lower()


def test_projects_active_fill_and_truthful_unrealized_shadow_state(tmp_path: Path) -> None:
    # Given: an exact modern ARMED decision binding and ACTIVE shadow fill.
    outputs = tmp_path / "outputs"
    armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(armed)
    active = _shadow(KrDayCapsuleShadowStatus.ACTIVE, armed.event_id, armed.observed_at + dt.timedelta(minutes=1))
    assert KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3").append(active)

    # When: the immutable ledgers are joined.
    projection = project_day_agent_facade(outputs, now=active.occurred_at + dt.timedelta(seconds=4))

    # Then: fill/time and risk levels are exact, while current price and unrealized P&L remain unavailable.
    rendered = _rendered(_lifecycle_items(projection.markets))
    assert all(value in rendered for value in ("ACTIVE", "fill 71100", "stop 70000", "targets 72500/74000"))
    assert f"fill time {active.occurred_at.isoformat()}" in rendered
    assert "unrealized unavailable (no current-price evidence)" in rendered
    assert "current price" not in rendered.lower()


@pytest.mark.parametrize("status", [KrDayDecisionStatus.REJECTED, KrDayDecisionStatus.BLOCKED])
def test_projects_rejected_and_blocked_exact_reasons(status: KrDayDecisionStatus, tmp_path: Path) -> None:
    # Given: a terminal pre-entry disposition with exact reasons and evidence refs.
    outputs = tmp_path / "outputs"
    decision = _decision(
        status,
        reasons=(KrDayDecisionReasonCode.MARKET_GATE_BLOCKED, KrDayDecisionReasonCode.SPREAD_TOO_WIDE),
    )
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(decision)

    # When: the dashboard projects the disposition.
    projection = project_day_agent_facade(outputs, now=decision.observed_at + dt.timedelta(seconds=5))

    # Then: exact reason codes and immutable evidence are visible.
    rendered = _rendered(_lifecycle_items(projection.markets))
    assert status.value in rendered
    assert "MARKET_GATE_BLOCKED,SPREAD_TOO_WIDE" in rendered
    assert "bar://005930/2026-08-24T01:02Z" in rendered


@pytest.mark.parametrize(
    "terminal_status",
    [KrDayCapsuleShadowStatus.STOPPED, KrDayCapsuleShadowStatus.TARGETED, KrDayCapsuleShadowStatus.CENSORED],
)
def test_projects_terminal_outcome_and_immutable_timeline(
    terminal_status: KrDayCapsuleShadowStatus, tmp_path: Path
) -> None:
    # Given: an ARMED thesis and a canonical immutable shadow lifecycle.
    outputs = tmp_path / "outputs"
    armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(armed)
    shadow_store = KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3")
    if terminal_status is KrDayCapsuleShadowStatus.CENSORED:
        first = _shadow(
            KrDayCapsuleShadowStatus.REGISTERED,
            armed.event_id,
            armed.observed_at + dt.timedelta(minutes=1),
        )
    else:
        first = _shadow(KrDayCapsuleShadowStatus.ACTIVE, armed.event_id, armed.observed_at + dt.timedelta(minutes=1))
    terminal = _shadow(
        terminal_status,
        armed.event_id,
        first.occurred_at + dt.timedelta(minutes=1),
        previous_event_id=first.event_id,
    )
    assert shadow_store.append(first) and shadow_store.append(terminal)

    # When: the dashboard joins and traces the lifecycle.
    projection = project_day_agent_facade(outputs, now=terminal.occurred_at + dt.timedelta(seconds=7))

    # Then: the actual outcome and every immutable event remain linked by trace edges.
    rendered = _rendered(_lifecycle_items(projection.markets))
    assert terminal_status.value.upper() in rendered
    assert terminal.reason.value in rendered
    refs = {node.safe_ref for node in projection.nodes}
    assert {armed.event_id, first.event_id, terminal.event_id} <= refs
    assert any(edge.kind == "derived_from" for edge in projection.edges)


def test_corrupt_decision_ledger_isolated_from_us_and_kr_shadow_summary(tmp_path: Path) -> None:
    # Given: valid US/report evidence, valid KR shadow evidence, and a corrupt KR decision ledger.
    outputs = tmp_path / "outputs"
    _publish(outputs / "us_day" / "close_reports", _report(_payload()))
    _publish(outputs / "kr_day" / "close_reports", _report(_payload(MarketId.KR_EQUITIES)))
    legacy = _shadow(KrDayCapsuleShadowStatus.ACTIVE, None, NOW)
    assert KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3").append(legacy)
    corrupt = outputs / "kr_day" / "kr-day-decisions.sqlite3"
    corrupt.write_text("not sqlite", encoding="utf-8")
    corrupt.chmod(0o600)

    # When: the facade reads each immutable source independently.
    projection = project_day_agent_facade(outputs, now=NOW + dt.timedelta(minutes=1))

    # Then: only lifecycle cards fail closed; US and KR report/shadow summaries remain truthful.
    us = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.us"))
    summary = next(item for item in projection.markets if item.item_id == "day_agent.kr.shadow")
    lifecycle = _lifecycle_items(projection.markets)
    assert us and all(item.state in {"populated", "empty"} for item in us)
    assert summary.state == "populated"
    assert lifecycle and all(item.state == "corrupt" for item in lifecycle)
    assert "decision ledger corrupt" in _rendered(lifecycle)


def test_legacy_unbound_active_shadow_fails_closed_without_borrowing_decision(tmp_path: Path) -> None:
    # Given: one modern decision and one legacy unbound ACTIVE event from another capsule.
    outputs = tmp_path / "outputs"
    decision = _decision(KrDayDecisionStatus.INVESTIGATING)
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(decision)
    legacy = _shadow(KrDayCapsuleShadowStatus.ACTIVE, None, decision.observed_at, capsule_id="f" * 64)
    assert KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3").append(legacy)

    # When: the dashboard projects both sources.
    projection = project_day_agent_facade(outputs, now=decision.observed_at + dt.timedelta(seconds=1))

    # Then: the legacy row is explicitly unbound and never borrows capsule, hypothesis, or prices.
    legacy_item = next(item for item in projection.markets if item.item_id == "day_agent.kr.lifecycle.unbound")
    assert legacy_item.state == "blocked"
    assert legacy_item.value == "legacy shadow unbound · no recommendation claim · SHADOW/PAPER ONLY"
    assert "71100" not in legacy_item.value


def test_contradictory_active_and_blocked_history_fails_closed(tmp_path: Path) -> None:
    # Given: an ARMED fill followed by a contradictory pre-entry BLOCKED decision.
    outputs = tmp_path / "outputs"
    armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    blocked = _decision(
        KrDayDecisionStatus.BLOCKED,
        previous_event_id=armed.event_id,
        reasons=(KrDayDecisionReasonCode.MARKET_GATE_BLOCKED,),
    )
    decisions = KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3")
    assert decisions.append(armed) and decisions.append(blocked)
    active = _shadow(KrDayCapsuleShadowStatus.ACTIVE, armed.event_id, armed.observed_at + dt.timedelta(minutes=1))
    assert KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3").append(active)

    # When: the dashboard joins the contradictory immutable history.
    projection = project_day_agent_facade(outputs, now=active.occurred_at + dt.timedelta(seconds=1))

    # Then: KR lifecycle alone is corrupt and no ACTIVE recommendation is rendered.
    lifecycle = _lifecycle_items(projection.markets)
    assert lifecycle and all(item.state == "corrupt" for item in lifecycle)
    assert "decision/shadow binding corrupt" in _rendered(lifecycle)
    assert all(item.kind != "day_recommendation" for item in lifecycle)


def test_full_snapshot_accepts_kr_immutable_timeline(tmp_path: Path) -> None:
    # Given: a modern ARMED decision and exact ACTIVE shadow binding.
    outputs = tmp_path / "outputs"
    armed = _decision(KrDayDecisionStatus.ARMED, reasons=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,))
    active = _shadow(KrDayCapsuleShadowStatus.ACTIVE, armed.event_id, armed.observed_at + dt.timedelta(minutes=1))
    assert KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3").append(armed)
    assert KrDayCapsuleShadowStore(outputs / "kr_day" / "kr-day-capsule-shadow.sqlite3").append(active)

    # When: the real dashboard snapshot surface validates trace reachability.
    snapshot = collect_dashboard_snapshot_v2(outputs, now=active.occurred_at + dt.timedelta(seconds=1))

    # Then: the recommendation reaches both source and immutable lifecycle terminals.
    card = next(item for item in snapshot.workspaces.markets.items if item.item_id.startswith("day_agent.kr.lifecycle"))
    assert card.kind == "day_recommendation"
    assert {armed.event_id, active.event_id} <= {node.safe_ref for node in snapshot.traces.nodes}


def _publish(root: Path, report: MarketCloseReport) -> None:
    _, created = publish_market_close_report(root, report)
    assert created


def _decision(
    status: KrDayDecisionStatus,
    *,
    previous_event_id: str | None = None,
    completed_bar_at: dt.datetime | None = None,
    reasons: tuple[KrDayDecisionReasonCode, ...] = (KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,),
    evidence: tuple[KrDayDecisionEvidenceValue, ...] = (),
) -> KrDayDecisionEvent:
    plan = _plan(valid_until=(completed_bar_at + dt.timedelta(minutes=8)) if completed_bar_at else None)
    payload = _decision_payload(
        status=status,
        plan=plan if status is KrDayDecisionStatus.ARMED else None,
        reason_codes=reasons,
        previous_event_id=previous_event_id,
        completed_bar_at=completed_bar_at,
        valid_until=plan.valid_until,
    )
    payload = KrDayDecisionEventPayload.model_validate(
        payload.model_dump(mode="python") | {"observed_evidence": evidence}
    )
    return KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python") | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )


def _shadow(
    status: KrDayCapsuleShadowStatus,
    decision_event_id: str | None,
    occurred_at: dt.datetime,
    *,
    previous_event_id: str | None = None,
    capsule_id: str = HEX_A,
) -> KrDayCapsuleShadowEvent:
    reasons = {
        KrDayCapsuleShadowStatus.REGISTERED: KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING,
        KrDayCapsuleShadowStatus.ACTIVE: KrDayCapsuleShadowReason.ENTRY,
        KrDayCapsuleShadowStatus.STOPPED: KrDayCapsuleShadowReason.STOP_FIRST,
        KrDayCapsuleShadowStatus.TARGETED: KrDayCapsuleShadowReason.TARGET,
        KrDayCapsuleShadowStatus.CENSORED: KrDayCapsuleShadowReason.BAR_GAP,
        KrDayCapsuleShadowStatus.BLOCKED: KrDayCapsuleShadowReason.SIGNAL_BLOCKED,
        KrDayCapsuleShadowStatus.FAILED: KrDayCapsuleShadowReason.INVALID_EVALUATION,
    }
    has_position = status in {
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowStatus.TARGETED,
    }
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id=capsule_id,
        evaluation_id=hashlib.sha256(f"{status}:{occurred_at.isoformat()}:{capsule_id}".encode()).hexdigest(),
        session_date=dt.date(2026, 8, 24),
        calendar_snapshot_id="calendar-1",
        collection_cycle_id="cycle-1",
        symbol="005930",
        attempted_bar_cursor=occurred_at,
        accepted_bar_cursor=(
            None
            if status
            in {
                KrDayCapsuleShadowStatus.CENSORED,
                KrDayCapsuleShadowStatus.BLOCKED,
                KrDayCapsuleShadowStatus.FAILED,
            }
            else occurred_at
        ),
        previous_event_id=previous_event_id,
        status=status,
        reason=reasons[status],
        signal_id=(
            "legacy-shadow-signal"
            if decision_event_id is None and has_position
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
        payload.model_dump(mode="python") | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )


def _lifecycle_items(items: tuple[WorkspaceItemV2, ...]) -> tuple[WorkspaceItemV2, ...]:
    return tuple(item for item in items if item.item_id.startswith("day_agent.kr.lifecycle"))


def _rendered(items: tuple[WorkspaceItemV2, ...]) -> str:
    return " | ".join(item.value or "" for item in items)
