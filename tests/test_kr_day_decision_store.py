from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

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
def _event(
    *,
    status: KrDayDecisionStatus = KrDayDecisionStatus.ARMED,
    reason_codes: tuple[KrDayDecisionReasonCode, ...] = (
        KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,
    ),
    previous_event_id: str | None = None,
    rationale: str = "Confirmed completed-bar setup",
) -> KrDayDecisionEvent:
    completed = dt.datetime(2026, 8, 24, 1, 2, tzinfo=UTC)
    deadline = completed + dt.timedelta(minutes=8)
    plan = None
    if status == KrDayDecisionStatus.ARMED:
        plan = KrDayConditionalPlan(
            trigger_rule="Close above the completed-bar resistance",
            trigger_price=Decimal("71000"),
            stop_price=Decimal("70000"),
            target_prices=(Decimal("72500"), Decimal("74000")),
            invalidation_rule="Cancel if a completed bar closes below the stop",
            valid_until=deadline,
            rationale=rationale,
            evidence_refs=("bar://005930/2026-08-24T01:02Z", "source://catalyst/42"),
            capsule_id=HEX_A,
            hypothesis_version_id=HEX_B,
        )
    payload = KrDayDecisionEventPayload(
        capsule_id=HEX_A,
        hypothesis_version_id=HEX_B,
        opportunity_id=HEX_C,
        session_date=dt.date(2026, 8, 24),
        symbol="005930",
        completed_bar_at=completed,
        observed_at=completed + dt.timedelta(seconds=2),
        valid_until=deadline,
        status=status,
        reason_codes=reason_codes,
        conditional_plan=plan,
        evidence_refs=("bar://005930/2026-08-24T01:02Z", "source://catalyst/42"),
        previous_event_id=previous_event_id,
    )
    return KrDayDecisionEvent(
        **payload.model_dump(mode="python"),
        event_id=KrDayDecisionEvent.canonical_id_for(payload),
    )


def test_model_requires_canonical_identity_and_frozen_plan_shape() -> None:
    # Given: a fully specified conditional pre-entry decision.
    event = _event()

    # When: the payload is serialized canonically.
    payload = KrDayDecisionEventPayload.model_validate(
        event.model_dump(mode="python", exclude={"event_id"})
    )

    # Then: its identity is the canonical JSON SHA-256 and the graph is immutable.
    expected = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    assert event.event_id == expected
    assert event.model_config["frozen"] is True
    assert event.model_config["extra"] == "forbid"
    assert event.model_config["revalidate_instances"] == "always"
    with pytest.raises(ValidationError):
        KrDayDecisionEvent(**(event.model_dump(mode="python") | {"event_id": HEX_C}))


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
    # Given: an ARMED event carrying a conditional plan.
    event = _event()

    # When/Then: changing to any other pre-entry status cannot preserve that plan.
    with pytest.raises(ValidationError):
        KrDayDecisionEventPayload(
            **(event.model_dump(mode="python", exclude={"event_id", "status"}) | {"status": status})
        )


def test_plan_rejects_illegal_prices_and_noncanonical_evidence() -> None:
    # Given: otherwise valid plan fields.
    event = _event()
    assert event.conditional_plan is not None
    fields = event.conditional_plan.model_dump(mode="python")

    # When/Then: a stop above entry and duplicate unsorted evidence are rejected.
    with pytest.raises(ValidationError):
        KrDayConditionalPlan(
            **(
                fields
                | {
                    "stop_price": Decimal("72000"),
                    "evidence_refs": ("source://z", "source://a", "source://a"),
                }
            )
        )


def test_expired_status_requires_elapsed_deadline() -> None:
    # Given: a future-valid INVESTIGATING payload.
    event = _event(status=KrDayDecisionStatus.INVESTIGATING)
    fields = event.model_dump(mode="python", exclude={"event_id", "status"})

    # When/Then: it cannot claim EXPIRED before its deadline.
    with pytest.raises(ValidationError):
        KrDayDecisionEventPayload(**(fields | {"status": KrDayDecisionStatus.EXPIRED}))


def test_append_replay_and_restart_are_deterministic(tmp_path: Path) -> None:
    # Given: a new private decision store and one canonical event.
    path = tmp_path / "private" / "decisions.sqlite3"
    event = _event()

    # When: it is appended, replayed, and read through a fresh store instance.
    first = KrDayDecisionStore(path).append(event)
    replay = KrDayDecisionStore(path).append(event)
    persisted = KrDayDecisionStore(path).events()

    # Then: only the exact first append persists and the file is owner-private.
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

    # Then: both immutable transitions and their lineage remain observable.
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


@pytest.mark.parametrize("corruption", ["payload", "schema"])
def test_corrupt_store_fails_closed(tmp_path: Path, corruption: str) -> None:
    # Given: a valid persisted event whose payload or schema is then corrupted.
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

    # When/Then: the reader refuses the entire untrusted store.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        store.events()


def test_unsafe_store_file_shape_fails_closed(tmp_path: Path) -> None:
    # Given: a directory occupying the configured database path.
    path = tmp_path / "private" / "decisions.sqlite3"
    path.mkdir(parents=True, mode=0o700)

    # When/Then: no SQLite operation is attempted through the unsafe shape.
    with pytest.raises(InvalidKrDayDecisionStoreError):
        KrDayDecisionStore(path).events()
