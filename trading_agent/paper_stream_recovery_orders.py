from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from decimal import Decimal

from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_stream_recovery_models import (
    InvalidPaperStreamRecoveryError,
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryConflictError,
    PaperStreamRecoveryKey,
    StoredPaperRecoveryOrder,
)

type RecoveryOrderValues = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str,
    int,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]
type RecoveryOrderRow = tuple[int, *RecoveryOrderValues]


def append_recovery_orders(
    connection: sqlite3.Connection,
    recovery_key: PaperStreamRecoveryKey,
    observations: tuple[PaperRecoveryOrderObservation, ...],
) -> None:
    expected = tuple(
        sorted(
            (_order_values(recovery_key, observation) for observation in observations),
            key=lambda values: values[2],
        )
    )
    existing: list[RecoveryOrderValues] = connection.execute(
        "SELECT * FROM paper_recovery_orders WHERE recovery_key = ? ORDER BY broker_order_id",
        (recovery_key,),
    ).fetchall()
    if existing:
        if tuple(existing) != expected:
            raise PaperStreamRecoveryConflictError
        return
    connection.executemany(
        "INSERT INTO paper_recovery_orders VALUES (" + ",".join("?" for _ in range(22)) + ")",
        expected,
    )


def recovery_orders_sha256(
    observations: tuple[PaperRecoveryOrderObservation, ...],
) -> str:
    values = tuple(
        sorted(
            (
                _order_values(PaperStreamRecoveryKey(""), observation)[1:]
                for observation in observations
            ),
            key=lambda item: item[1],
        )
    )
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_recovery_orders(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperRecoveryOrder, ...]:
    rows: list[RecoveryOrderRow] = connection.execute(
        """SELECT recovery.rowid, orders.*
        FROM paper_recovery_orders AS orders
        JOIN paper_stream_recoveries AS recovery
        ON recovery.recovery_key = orders.recovery_key
        ORDER BY recovery.rowid, orders.broker_order_id"""
    ).fetchall()
    return tuple(_stored_order(row) for row in rows)


def _order_values(
    recovery_key: PaperStreamRecoveryKey,
    observation: PaperRecoveryOrderObservation,
) -> RecoveryOrderValues:
    order = observation.order
    return (
        recovery_key,
        observation.source.value,
        order.broker_order_id,
        order.client_order_id,
        order.symbol,
        order.side.value,
        order.status,
        str(order.quantity),
        str(order.filled_quantity),
        _decimal_text(order.filled_average_price),
        _decimal_text(order.limit_price),
        order.time_in_force,
        int(order.extended_hours),
        _instant_text(order.created_at),
        _instant_text(order.updated_at),
        _instant_text(order.submitted_at),
        _instant_text(order.filled_at),
        _instant_text(order.canceled_at),
        _instant_text(order.failed_at),
        _instant_text(order.replaced_at),
        order.replaced_by_order_id,
        order.replaces_order_id,
    )


def _stored_order(row: RecoveryOrderRow) -> StoredPaperRecoveryOrder:
    return StoredPaperRecoveryOrder(
        recovery_id=row[0],
        recovery_key=PaperStreamRecoveryKey(row[1]),
        source=PaperRecoveryOrderSource(row[2]),
        order=PaperOrderSnapshot(
            broker_order_id=BrokerOrderId(row[3]),
            client_order_id=IntentId(row[4]),
            symbol=row[5],
            side=PaperOrderSide(row[6]),
            status=row[7],
            quantity=Decimal(row[8]),
            filled_quantity=Decimal(row[9]),
            filled_average_price=_optional_decimal(row[10]),
            limit_price=_optional_decimal(row[11]),
            time_in_force=row[12],
            extended_hours=bool(row[13]),
            created_at=_optional_instant(row[14]),
            updated_at=_optional_instant(row[15]),
            submitted_at=_optional_instant(row[16]),
            filled_at=_optional_instant(row[17]),
            canceled_at=_optional_instant(row[18]),
            failed_at=_optional_instant(row[19]),
            replaced_at=_optional_instant(row[20]),
            replaced_by_order_id=(
                None if row[21] is None else BrokerOrderId(row[21])
            ),
            replaces_order_id=(
                None if row[22] is None else BrokerOrderId(row[22])
            ),
        ),
    )


def _instant_text(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPaperStreamRecoveryError
    return value.isoformat()


def _optional_instant(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidPaperStreamRecoveryError
    return instant


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
