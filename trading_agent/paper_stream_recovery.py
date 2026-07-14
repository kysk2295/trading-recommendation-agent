from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_stream_recovery_models import (
    InvalidPaperStreamRecoveryError,
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperRecoveryState,
    PaperStreamRecoveryConflictError,
    PaperStreamRecoveryKey,
    PaperStreamRecoveryObservation,
    StoredPaperRecoveryOrder,
    StoredPaperStreamRecovery,
)
from trading_agent.paper_stream_recovery_orders import (
    append_recovery_orders,
    read_recovery_orders,
    recovery_orders_sha256,
)
from trading_agent.paper_stream_recovery_schema import CREATE_PAPER_STREAM_RECOVERY_SCHEMA

type PaperStreamRecoveryValues = tuple[
    str, str, str, str, str, str, str, str, int
]
type PaperStreamRecoveryRow = tuple[
    int, str, str, str, str, str, str, str, str, int
]
_TRANSACTION_FAILURES = (Exception,)


def append_paper_stream_recovery(
    connection: sqlite3.Connection,
    observation: PaperStreamRecoveryObservation,
) -> bool:
    _require_observation(observation)
    snapshot_hash = hashlib.sha256(observation.snapshot_json.encode()).hexdigest()
    orders_hash = recovery_orders_sha256(observation.orders)
    recovery_key = _recovery_key(observation, snapshot_hash, orders_hash)
    values = (
        recovery_key,
        observation.account_fingerprint,
        observation.connection_epoch,
        observation.started_at.isoformat(),
        observation.completed_at.isoformat(),
        observation.snapshot_json,
        snapshot_hash,
        orders_hash,
        int(observation.execution_detail_complete),
    )
    bracket_row: tuple[str] | None = connection.execute(
        """SELECT recovery_key FROM paper_stream_recoveries
        WHERE account_fingerprint = ? AND connection_epoch = ?
        AND started_at = ? AND completed_at = ?""",
        (
            observation.account_fingerprint,
            observation.connection_epoch,
            observation.started_at.isoformat(),
            observation.completed_at.isoformat(),
        ),
    ).fetchone()
    if bracket_row is not None and bracket_row[0] != recovery_key:
        raise PaperStreamRecoveryConflictError
    existing: PaperStreamRecoveryValues | None = connection.execute(
        "SELECT * FROM paper_stream_recoveries WHERE recovery_key = ?",
        (recovery_key,),
    ).fetchone()
    if existing is not None:
        if existing != values:
            raise PaperStreamRecoveryConflictError
        append_recovery_orders(connection, recovery_key, observation.orders)
        return False
    try:
        _ = connection.execute(
            "INSERT INTO paper_stream_recoveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        append_recovery_orders(connection, recovery_key, observation.orders)
        connection.commit()
    except _TRANSACTION_FAILURES:
        connection.rollback()
        raise
    return True


def read_paper_stream_recoveries(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperStreamRecovery, ...]:
    rows: list[PaperStreamRecoveryRow] = connection.execute(
        "SELECT rowid, * FROM paper_stream_recoveries ORDER BY rowid"
    ).fetchall()
    return tuple(_stored_recovery(row) for row in rows)


def read_paper_recovery_orders(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperRecoveryOrder, ...]:
    orders = read_recovery_orders(connection)
    for recovery in read_paper_stream_recoveries(connection):
        observations = tuple(
            PaperRecoveryOrderObservation(order.source, order.order)
            for order in orders
            if order.recovery_key == recovery.recovery_key
        )
        if recovery_orders_sha256(observations) != recovery.orders_sha256:
            raise InvalidPaperStreamRecoveryError
    return orders


def recovery_completed_at(recovery: StoredPaperStreamRecovery) -> dt.datetime:
    return _aware_instant(recovery.completed_at)


def _stored_recovery(row: PaperStreamRecoveryRow) -> StoredPaperStreamRecovery:
    started_at = _aware_instant(row[4])
    completed_at = _aware_instant(row[5])
    snapshot_hash = hashlib.sha256(row[6].encode()).hexdigest()
    observation = PaperStreamRecoveryObservation(
        AccountFingerprint(row[2]),
        row[3],
        started_at,
        completed_at,
        row[6],
        bool(row[9]),
    )
    _require_observation(observation)
    if (
        row[7] != snapshot_hash
        or PaperStreamRecoveryKey(row[1])
        != _recovery_key(observation, snapshot_hash, row[8])
    ):
        raise InvalidPaperStreamRecoveryError
    return StoredPaperStreamRecovery(
        recovery_id=row[0],
        recovery_key=PaperStreamRecoveryKey(row[1]),
        account_fingerprint=AccountFingerprint(row[2]),
        connection_epoch=row[3],
        started_at=row[4],
        completed_at=row[5],
        snapshot_json=row[6],
        snapshot_sha256=snapshot_hash,
        orders_sha256=row[8],
        execution_detail_complete=bool(row[9]),
    )


def _recovery_key(
    observation: PaperStreamRecoveryObservation,
    snapshot_hash: str,
    orders_hash: str,
) -> PaperStreamRecoveryKey:
    material = "\x00".join(
        (
            observation.account_fingerprint,
            observation.connection_epoch,
            observation.started_at.isoformat(),
            observation.completed_at.isoformat(),
            snapshot_hash,
            orders_hash,
            str(int(observation.execution_detail_complete)),
        )
    )
    return PaperStreamRecoveryKey(
        f"alpaca:recovery:{hashlib.sha256(material.encode()).hexdigest()}"
    )


def _require_observation(observation: PaperStreamRecoveryObservation) -> None:
    order_ids = tuple(order.order.broker_order_id for order in observation.orders)
    if (
        not observation.account_fingerprint
        or not observation.connection_epoch
        or not observation.snapshot_json
        or not _is_aware(observation.started_at)
        or not _is_aware(observation.completed_at)
        or observation.started_at >= observation.completed_at
        or len(order_ids) != len(set(order_ids))
    ):
        raise InvalidPaperStreamRecoveryError


def _aware_instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if not _is_aware(instant):
        raise InvalidPaperStreamRecoveryError
    return instant


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "CREATE_PAPER_STREAM_RECOVERY_SCHEMA",
    "InvalidPaperStreamRecoveryError",
    "PaperRecoveryOrderObservation",
    "PaperRecoveryOrderSource",
    "PaperRecoveryState",
    "PaperStreamRecoveryConflictError",
    "PaperStreamRecoveryKey",
    "PaperStreamRecoveryObservation",
    "StoredPaperRecoveryOrder",
    "StoredPaperStreamRecovery",
    "append_paper_stream_recovery",
    "read_paper_recovery_orders",
    "read_paper_stream_recoveries",
    "recovery_completed_at",
)
