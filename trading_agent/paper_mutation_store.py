from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    PaperOrderSide,
)
from trading_agent.paper_mutation_keys import (
    PaperMutationEventKey,
    PaperMutationKey,
    paper_mutation_event_key,
    paper_mutation_key,
)
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_source_validation import require_mutation_source
from trading_agent.paper_mutation_transitions import (
    InvalidPaperMutationTransitionError,
    require_mutation_transition,
)
from trading_agent.paper_mutation_validation import (
    InvalidPaperMutationRecordError,
    require_mutation_event,
    require_mutation_intent,
)

type MutationIntentRow = tuple[
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    int | None,
    str,
    str,
    str | None,
    str | None,
    str | None,
]
type MutationEventRow = tuple[
    int,
    str,
    str,
    int,
    str,
    str,
    str | None,
    int | None,
    str | None,
    str,
]
type MutationEventValues = tuple[
    str,
    str,
    int,
    str,
    str,
    str | None,
    int | None,
    str | None,
    str,
]


class PaperMutationConflictError(RuntimeError):
    def __str__(self) -> str:
        return "같은 Paper mutation identity의 immutable 값이 다릅니다"


@dataclass(frozen=True, slots=True)
class StoredPaperMutationIntent:
    mutation_key: PaperMutationKey
    intent: PaperMutationIntent


@dataclass(frozen=True, slots=True)
class StoredPaperMutationEvent:
    event_id: int
    event_key: PaperMutationEventKey
    mutation_key: PaperMutationKey
    event: PaperMutationEvent


def save_paper_mutation_intent(
    connection: sqlite3.Connection,
    intent: PaperMutationIntent,
) -> bool:
    require_mutation_intent(intent)
    require_mutation_source(connection, intent)
    mutation_key = paper_mutation_key(intent)
    values = _intent_values(mutation_key, intent)
    existing: MutationIntentRow | None = connection.execute(
        "SELECT * FROM paper_mutation_intents WHERE mutation_key = ?",
        (mutation_key,),
    ).fetchone()
    if existing is not None:
        if existing != values:
            raise PaperMutationConflictError
        return False
    identity: MutationIntentRow | None = connection.execute(
        """SELECT * FROM paper_mutation_intents
        WHERE operation = ?
          AND IFNULL(protective_plan_key, '') = IFNULL(?, '')
          AND IFNULL(safety_plan_key, '') = IFNULL(?, '')
          AND IFNULL(action_sequence, -1) = IFNULL(?, -1)""",
        (
            intent.operation.value,
            intent.protective_plan_key,
            intent.safety_plan_key,
            intent.action_sequence,
        ),
    ).fetchone()
    if identity is not None:
        raise PaperMutationConflictError
    _ = connection.execute(
        "INSERT INTO paper_mutation_intents VALUES (" + ",".join("?" for _ in range(12)) + ")",
        values,
    )
    connection.commit()
    return True


def append_paper_mutation_event(
    connection: sqlite3.Connection,
    mutation_key: PaperMutationKey,
    event: PaperMutationEvent,
) -> bool:
    require_mutation_event(event)
    intent_exists = connection.execute(
        "SELECT 1 FROM paper_mutation_intents WHERE mutation_key = ?",
        (mutation_key,),
    ).fetchone()
    if intent_exists is None:
        raise InvalidPaperMutationTransitionError
    event_key = paper_mutation_event_key(mutation_key, event)
    values = _event_values(event_key, mutation_key, event)
    existing: MutationEventRow | None = connection.execute(
        "SELECT * FROM paper_mutation_events WHERE event_key = ?",
        (event_key,),
    ).fetchone()
    if existing is not None:
        if existing[1:] != values:
            raise PaperMutationConflictError
        return False
    identity: MutationEventRow | None = connection.execute(
        """SELECT * FROM paper_mutation_events
        WHERE mutation_key = ? AND attempt_number = ? AND event_type = ?""",
        (mutation_key, event.attempt_number, event.event_type.value),
    ).fetchone()
    if identity is not None:
        raise PaperMutationConflictError
    prior = tuple(
        stored.event for stored in read_paper_mutation_events(connection) if stored.mutation_key == mutation_key
    )
    require_mutation_transition(prior, event)
    _ = connection.execute(
        """INSERT INTO paper_mutation_events
        (event_key, mutation_key, attempt_number, occurred_at, event_type,
         request_id, status_code, broker_order_id, evidence_sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )
    connection.commit()
    return True


def read_paper_mutation_intents(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperMutationIntent, ...]:
    rows: list[MutationIntentRow] = connection.execute("SELECT * FROM paper_mutation_intents ORDER BY rowid").fetchall()
    return tuple(_stored_intent(row) for row in rows)


def read_paper_mutation_events(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperMutationEvent, ...]:
    rows: list[MutationEventRow] = connection.execute(
        "SELECT * FROM paper_mutation_events ORDER BY event_id"
    ).fetchall()
    return tuple(_stored_event(row) for row in rows)


def _intent_values(
    mutation_key: PaperMutationKey,
    intent: PaperMutationIntent,
) -> MutationIntentRow:
    return (
        mutation_key,
        intent.account_fingerprint,
        intent.created_at.isoformat(),
        intent.operation.value,
        intent.protective_plan_key,
        intent.safety_plan_key,
        intent.action_sequence,
        intent.request_sha256,
        intent.symbol,
        intent.broker_order_id,
        None if intent.side is None else intent.side.value,
        None if intent.quantity is None else str(intent.quantity),
    )


def _event_values(
    event_key: PaperMutationEventKey,
    mutation_key: PaperMutationKey,
    event: PaperMutationEvent,
) -> MutationEventValues:
    return (
        event_key,
        mutation_key,
        event.attempt_number,
        event.occurred_at.isoformat(),
        event.event_type.value,
        event.request_id,
        event.status_code,
        event.broker_order_id,
        event.evidence_sha256,
    )


def _stored_intent(row: MutationIntentRow) -> StoredPaperMutationIntent:
    intent = PaperMutationIntent(
        AccountFingerprint(row[1]),
        dt.datetime.fromisoformat(row[2]),
        PaperMutationOperation(row[3]),
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        None if row[9] is None else BrokerOrderId(row[9]),
        None if row[10] is None else PaperOrderSide(row[10]),
        None if row[11] is None else Decimal(row[11]),
    )
    require_mutation_intent(intent)
    if paper_mutation_key(intent) != row[0]:
        raise InvalidPaperMutationRecordError
    return StoredPaperMutationIntent(PaperMutationKey(row[0]), intent)


def _stored_event(row: MutationEventRow) -> StoredPaperMutationEvent:
    event = PaperMutationEvent(
        row[3],
        dt.datetime.fromisoformat(row[4]),
        PaperMutationEventType(row[5]),
        row[6],
        row[7],
        None if row[8] is None else BrokerOrderId(row[8]),
        row[9],
    )
    require_mutation_event(event)
    mutation_key = PaperMutationKey(row[2])
    if paper_mutation_event_key(mutation_key, event) != row[1]:
        raise InvalidPaperMutationRecordError
    return StoredPaperMutationEvent(
        row[0],
        PaperMutationEventKey(row[1]),
        mutation_key,
        event,
    )
