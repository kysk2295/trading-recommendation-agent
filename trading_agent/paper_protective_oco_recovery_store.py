from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import override

from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_store import ProtectiveOcoPlanKey
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryKey

type ProtectiveOcoLegValues = tuple[
    str,
    str,
    str,
    str,
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
]
type ProtectiveOcoLegRow = tuple[int, *ProtectiveOcoLegValues]
type SnapshotLegValues = tuple[
    str,
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
]


class InvalidProtectiveOcoRecoveryError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "보호 OCO recovery leg가 저장된 계획과 일치하지 않습니다"


class ProtectiveOcoRecoveryConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 보호 OCO recovery leg의 immutable 필드가 다릅니다"


@dataclass(frozen=True, slots=True)
class StoredProtectiveOcoSnapshot:
    recovery_id: int
    recovery_key: PaperStreamRecoveryKey
    plan_key: ProtectiveOcoPlanKey
    snapshot: ProtectiveOcoSnapshot


def append_recovery_protective_ocos(
    connection: sqlite3.Connection,
    recovery_key: PaperStreamRecoveryKey,
    snapshots: tuple[ProtectiveOcoSnapshot, ...],
) -> None:
    expected = tuple(
        value
        for snapshot in sorted(
            snapshots,
            key=lambda item: item.take_profit.broker_order_id,
        )
        for value in _database_values(connection, recovery_key, snapshot)
    )
    existing: list[ProtectiveOcoLegValues] = connection.execute(
        """SELECT * FROM paper_recovery_protective_oco_legs
        WHERE recovery_key = ? ORDER BY parent_broker_order_id, leg_kind""",
        (recovery_key,),
    ).fetchall()
    if existing:
        if tuple(existing) != expected:
            raise ProtectiveOcoRecoveryConflictError
        return
    connection.executemany(
        "INSERT INTO paper_recovery_protective_oco_legs VALUES (" + ",".join("?" for _ in range(17)) + ")",
        expected,
    )


def recovery_protective_ocos_sha256(
    snapshots: tuple[ProtectiveOcoSnapshot, ...],
) -> str:
    values = tuple(
        value
        for snapshot in sorted(
            snapshots,
            key=lambda item: item.take_profit.broker_order_id,
        )
        for value in _snapshot_values(snapshot)
    )
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_recovery_protective_ocos(
    connection: sqlite3.Connection,
) -> tuple[StoredProtectiveOcoSnapshot, ...]:
    rows: list[ProtectiveOcoLegRow] = connection.execute(
        """SELECT recovery.rowid, legs.*
        FROM paper_recovery_protective_oco_legs AS legs
        JOIN paper_stream_recoveries AS recovery
        ON recovery.recovery_key = legs.recovery_key
        ORDER BY recovery.rowid, legs.parent_broker_order_id, legs.leg_kind"""
    ).fetchall()
    groups: dict[tuple[int, str, str, str], list[ProtectiveOcoLegRow]] = {}
    for row in rows:
        key = (row[0], row[1], row[2], row[3])
        groups.setdefault(key, []).append(row)
    return tuple(_stored_snapshot(key, tuple(group)) for key, group in groups.items())


def _database_values(
    connection: sqlite3.Connection,
    recovery_key: PaperStreamRecoveryKey,
    snapshot: ProtectiveOcoSnapshot,
) -> tuple[ProtectiveOcoLegValues, ProtectiveOcoLegValues]:
    plan_key = _matching_plan_key(connection, snapshot)
    parent_order_id = snapshot.take_profit.broker_order_id
    values = _snapshot_values(snapshot)
    return (
        (
            recovery_key,
            plan_key,
            parent_order_id,
            *values[0],
        ),
        (
            recovery_key,
            plan_key,
            parent_order_id,
            *values[1],
        ),
    )


def _snapshot_values(
    snapshot: ProtectiveOcoSnapshot,
) -> tuple[SnapshotLegValues, SnapshotLegValues]:
    return (
        _snapshot_leg_values(snapshot, snapshot.take_profit),
        _snapshot_leg_values(snapshot, snapshot.stop_loss),
    )


def _snapshot_leg_values(
    snapshot: ProtectiveOcoSnapshot,
    leg: ProtectiveOcoLegSnapshot,
) -> SnapshotLegValues:
    return (
        leg.kind.value,
        leg.broker_order_id,
        leg.client_order_id,
        snapshot.observed_at.isoformat(),
        leg.symbol,
        leg.side.value,
        leg.status,
        str(leg.quantity),
        str(leg.filled_quantity),
        leg.order_type.value,
        None if leg.limit_price is None else str(leg.limit_price),
        None if leg.stop_price is None else str(leg.stop_price),
        leg.time_in_force,
        int(leg.extended_hours),
    )


def _matching_plan_key(
    connection: sqlite3.Connection,
    snapshot: ProtectiveOcoSnapshot,
) -> ProtectiveOcoPlanKey:
    take_profit = snapshot.take_profit
    stop_loss = snapshot.stop_loss
    rows: list[tuple[str]] = connection.execute(
        """SELECT plan_key FROM protective_oco_plans
        WHERE client_order_id = ? AND symbol = ? AND side = ? AND quantity = ?
        AND take_profit_limit = ? AND stop_price = ?""",
        (
            take_profit.client_order_id,
            take_profit.symbol,
            take_profit.side.value,
            str(take_profit.quantity),
            str(take_profit.limit_price),
            str(stop_loss.stop_price),
        ),
    ).fetchall()
    if len(rows) != 1:
        raise InvalidProtectiveOcoRecoveryError
    return ProtectiveOcoPlanKey(rows[0][0])


def _stored_snapshot(
    key: tuple[int, str, str, str],
    rows: tuple[ProtectiveOcoLegRow, ...],
) -> StoredProtectiveOcoSnapshot:
    if len(rows) != 2 or rows[0][7] != rows[1][7]:
        raise InvalidProtectiveOcoRecoveryError
    legs = {_stored_leg(row).kind: _stored_leg(row) for row in rows}
    if set(legs) != {
        ProtectiveOcoLegKind.TAKE_PROFIT,
        ProtectiveOcoLegKind.STOP_LOSS,
    }:
        raise InvalidProtectiveOcoRecoveryError
    return StoredProtectiveOcoSnapshot(
        recovery_id=key[0],
        recovery_key=PaperStreamRecoveryKey(key[1]),
        plan_key=ProtectiveOcoPlanKey(key[2]),
        snapshot=ProtectiveOcoSnapshot(
            observed_at=dt.datetime.fromisoformat(rows[0][7]),
            take_profit=legs[ProtectiveOcoLegKind.TAKE_PROFIT],
            stop_loss=legs[ProtectiveOcoLegKind.STOP_LOSS],
        ),
    )


def _stored_leg(row: ProtectiveOcoLegRow) -> ProtectiveOcoLegSnapshot:
    return ProtectiveOcoLegSnapshot(
        kind=ProtectiveOcoLegKind(row[4]),
        broker_order_id=BrokerOrderId(row[5]),
        client_order_id=row[6],
        symbol=row[8],
        side=PaperOrderSide(row[9]),
        status=row[10],
        quantity=Decimal(row[11]),
        filled_quantity=Decimal(row[12]),
        order_type=ProtectiveOcoOrderType(row[13]),
        limit_price=None if row[14] is None else Decimal(row[14]),
        stop_price=None if row[15] is None else Decimal(row[15]),
        time_in_force=row[16],
        extended_hours=bool(row[17]),
    )
