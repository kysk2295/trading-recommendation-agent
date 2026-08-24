from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ConfigDict, ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_decision_models import (
    KrDayConditionalPlan,
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import (
    InvalidKrDayDecisionStoreError,
    KrDayDecisionStore,
)

UTC = dt.UTC
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
EVIDENCE = ("bar://005930/2026-08-24T01:02Z", "source://catalyst/42")


def _plan(
    *,
    valid_until: dt.datetime | None = None,
    stop_price: Decimal = Decimal("70000"),
    evidence_refs: tuple[str, ...] = EVIDENCE,
    rationale: str = "Confirmed completed-bar setup",
) -> KrDayConditionalPlan:
    deadline = valid_until or dt.datetime(2026, 8, 24, 1, 10, tzinfo=UTC)
    return KrDayConditionalPlan(
        trigger_rule="Close above the completed-bar resistance",
        trigger_price=Decimal("71000"),
        stop_price=stop_price,
        target_prices=(Decimal("72500"), Decimal("74000")),
        invalidation_rule="Cancel if a completed bar closes below the stop",
        valid_until=deadline,
        rationale=rationale,
        evidence_refs=evidence_refs,
        capsule_id=HEX_A,
        hypothesis_version_id=HEX_B,
    )


def _payload(
    *,
    status: KrDayDecisionStatus,
    plan: KrDayConditionalPlan | None = None,
    reason_codes: tuple[KrDayDecisionReasonCode, ...] = (
        KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,
    ),
    previous_event_id: str | None = None,
    completed_bar_at: dt.datetime | None = None,
    observed_at: dt.datetime | None = None,
    valid_until: dt.datetime | None = None,
) -> KrDayDecisionEventPayload:
    completed = completed_bar_at or dt.datetime(2026, 8, 24, 1, 2, tzinfo=UTC)
    observed = observed_at or completed + dt.timedelta(seconds=2)
    deadline = valid_until or completed + dt.timedelta(minutes=8)
    return KrDayDecisionEventPayload(
        capsule_id=HEX_A,
        hypothesis_version_id=HEX_B,
        opportunity_id=HEX_C,
        session_date=dt.date(2026, 8, 24),
        symbol="005930",
        completed_bar_at=completed,
        observed_at=observed,
        valid_until=deadline,
        status=status,
        reason_codes=reason_codes,
        conditional_plan=plan,
        evidence_refs=EVIDENCE,
        previous_event_id=previous_event_id,
    )


def _event(
    *,
    status: KrDayDecisionStatus = KrDayDecisionStatus.ARMED,
    reason_codes: tuple[KrDayDecisionReasonCode, ...] = (
        KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,
    ),
    previous_event_id: str | None = None,
    event_id: str | None = None,
) -> KrDayDecisionEvent:
    plan = _plan() if status == KrDayDecisionStatus.ARMED else None
    payload = _payload(
        status=status,
        plan=plan,
        reason_codes=reason_codes,
        previous_event_id=previous_event_id,
    )
    return _event_from_payload(payload, event_id or KrDayDecisionEvent.canonical_id_for(payload))


def _event_from_payload(payload: KrDayDecisionEventPayload, event_id: str) -> KrDayDecisionEvent:
    return KrDayDecisionEvent(
        schema_version=payload.schema_version,
        capsule_id=payload.capsule_id,
        hypothesis_version_id=payload.hypothesis_version_id,
        opportunity_id=payload.opportunity_id,
        session_date=payload.session_date,
        symbol=payload.symbol,
        completed_bar_at=payload.completed_bar_at,
        observed_at=payload.observed_at,
        valid_until=payload.valid_until,
        status=payload.status,
        reason_codes=payload.reason_codes,
        conditional_plan=payload.conditional_plan,
        evidence_refs=payload.evidence_refs,
        previous_event_id=payload.previous_event_id,
        research_only=payload.research_only,
        paper_only=payload.paper_only,
        trading_authority=payload.trading_authority,
        event_id=event_id,
    )


def test_model_requires_canonical_identity_and_frozen_plan_shape() -> None:
    # Given: a fully specified conditional pre-entry decision.
    event = _event()
    expected_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # When: its canonical payload identity is calculated.
    payload = _payload(status=KrDayDecisionStatus.ARMED, plan=_plan())

    # Then: identity and strict immutable configuration are enforced.
    expected = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    assert event.event_id == expected
    assert event.model_config == expected_config
    with pytest.raises(ValidationError):
        _event(event_id=HEX_C)


@pytest.mark.parametrize(
    "status",
    [
        KrDayDecisionStatus.INVESTIGATING,
        KrDayDecisionStatus.REJECTED,
        KrDayDecisionStatus.BLOCKED,
        KrDayDecisionStatus.EXPIRED,
    ],
)
def test_non_armed_status_rejects_conditional_plan(status: KrDayDecisionStatus) -> None:
    # Given: a conditional plan prepared for a non-ARMED status.
    plan = _plan()

    # When/Then: the payload refuses the plan.
    with pytest.raises(ValidationError):
        _payload(status=status, plan=plan)


def test_plan_rejects_illegal_prices_and_noncanonical_evidence() -> None:
    # Given: an invalid price ladder and duplicate unsorted evidence.
    evidence = ("source://z", "source://a", "source://a")

    # When/Then: the conditional plan rejects both illegal shapes.
    with pytest.raises(ValidationError):
        _plan(stop_price=Decimal("72000"), evidence_refs=evidence)


def test_expired_status_requires_elapsed_deadline() -> None:
    # Given: a future-valid deadline.
    deadline = dt.datetime(2026, 8, 24, 1, 10, tzinfo=UTC)

    # When/Then: a decision cannot claim EXPIRED before that deadline.
    with pytest.raises(ValidationError):
        _payload(status=KrDayDecisionStatus.EXPIRED, valid_until=deadline)


def test_deadline_cannot_precede_completed_bar() -> None:
    # Given: an EXPIRED decision whose deadline predates its completed bar.
    completed = dt.datetime(2026, 8, 24, 1, 2, tzinfo=UTC)

    # When/Then: the temporal contract rejects the reversed interval.
    with pytest.raises(ValidationError):
        _payload(
            status=KrDayDecisionStatus.EXPIRED,
            completed_bar_at=completed,
            observed_at=completed + dt.timedelta(seconds=2),
            valid_until=completed - dt.timedelta(seconds=1),
        )


def test_append_replay_and_restart_are_deterministic(tmp_path: Path) -> None:
    # Given: a new private decision store and one canonical event.
    path = tmp_path / "private" / "decisions.sqlite3"
    event = _event()

    # When: it is appended, replayed, and read through a fresh store instance.
    first = KrDayDecisionStore(path).append(event)
    replay = KrDayDecisionStore(path).append(event)
    persisted = KrDayDecisionStore(path).events()

    # Then: only the exact first append persists and the path is private.
    assert (first, replay, persisted) == (True, False, (event,))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_idempotency_key_conflict_fails_closed(tmp_path: Path) -> None:
    # Given: one persisted decision identity key.
    store = KrDayDecisionStore(tmp_path / "private" / "decisions.sqlite3")
    assert store.append(_event()) is True

    # When/Then: a different payload under the exact key is rejected.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        store.append(_event(reason_codes=(KrDayDecisionReasonCode.SPREAD_TOO_WIDE,)))


def test_different_status_same_bar_retains_lineage(tmp_path: Path) -> None:
    # Given: an INVESTIGATING event on one completed bar.
    store = KrDayDecisionStore(tmp_path / "private" / "decisions.sqlite3")
    first = _event(status=KrDayDecisionStatus.INVESTIGATING)
    assert store.append(first) is True
    second = _event(status=KrDayDecisionStatus.REJECTED, previous_event_id=first.event_id)

    # When: a different status is appended for the same bar.
    appended = store.append(second)

    # Then: both transitions and their lineage remain observable.
    assert appended is True
    assert store.events() == (first, second)
    assert store.latest(HEX_A, HEX_C, dt.date(2026, 8, 24)) == second
    assert store.event(first.event_id) == first


@pytest.mark.parametrize(
    "statement",
    ["UPDATE kr_day_decision_events SET symbol='000000'", "DELETE FROM kr_day_decision_events"],
)
def test_direct_sql_mutations_are_rejected(tmp_path: Path, statement: str) -> None:
    # Given: a store containing one event.
    path = tmp_path / "private" / "decisions.sqlite3"
    assert KrDayDecisionStore(path).append(_event()) is True

    # When/Then: SQLite itself rejects updates and deletes.
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(statement)


def test_same_named_permissive_trigger_fails_closed(tmp_path: Path) -> None:
    # Given: the UPDATE guard is replaced by a permissive same-name trigger.
    path = tmp_path / "private" / "decisions.sqlite3"
    store = KrDayDecisionStore(path)
    assert store.append(_event()) is True
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER kr_day_decision_events_no_update")
        connection.execute(
            "CREATE TRIGGER kr_day_decision_events_no_update BEFORE UPDATE ON "
            "kr_day_decision_events BEGIN SELECT 1; END"
        )

    # When/Then: exact schema identity rejects the replacement.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        store.events()


@pytest.mark.parametrize("corruption", ["payload", "schema"])
def test_corrupt_store_fails_closed(tmp_path: Path, corruption: str) -> None:
    # Given: a valid event whose payload or schema is corrupted.
    path = tmp_path / "private" / "decisions.sqlite3"
    store = KrDayDecisionStore(path)
    assert store.append(_event()) is True
    with sqlite3.connect(path) as connection:
        if corruption == "payload":
            connection.execute("DROP TRIGGER kr_day_decision_events_no_update")
            connection.execute("UPDATE kr_day_decision_events SET payload_json='{}'")
            connection.execute(
                "CREATE TRIGGER kr_day_decision_events_no_update BEFORE UPDATE ON "
                "kr_day_decision_events BEGIN SELECT RAISE(ABORT, 'append-only'); END"
            )
        else:
            connection.execute("PRAGMA user_version=99")

    # When/Then: the reader refuses the untrusted store.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        store.events()


def test_unsafe_store_file_shape_fails_closed(tmp_path: Path) -> None:
    # Given: a directory occupying the configured database path.
    path = tmp_path / "private" / "decisions.sqlite3"
    path.mkdir(parents=True, mode=0o700)

    # When/Then: no SQLite operation is attempted through the unsafe shape.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        KrDayDecisionStore(path).events()
