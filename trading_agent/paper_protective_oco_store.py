from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import NewType, override

from trading_agent.paper_execution_models import IntentId, PaperOrderSide
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
    ProtectiveOcoSnapshot,
)

ProtectiveOcoPlanKey = NewType("ProtectiveOcoPlanKey", str)

type ProtectiveOcoPlanValues = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    str,
    str,
    str,
    int,
]


class InvalidProtectiveOcoPlanError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "보호 OCO 계획이 실행 원장 계약과 일치하지 않습니다"


class ProtectiveOcoPlanConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 보호 OCO identity의 immutable 필드가 다릅니다"


@dataclass(frozen=True, slots=True)
class StoredProtectiveOcoPlan:
    plan_key: ProtectiveOcoPlanKey
    planned_at: str
    plan: ProtectiveOcoExitPlan


def save_protective_oco_plan(
    connection: sqlite3.Connection,
    plan: ProtectiveOcoExitPlan,
    planned_at: dt.datetime,
) -> bool:
    _require_plan(plan, planned_at)
    plan_key = protective_oco_plan_key(plan)
    values = _plan_values(plan_key, plan, planned_at)
    existing: ProtectiveOcoPlanValues | None = connection.execute(
        "SELECT * FROM protective_oco_plans WHERE plan_key = ?",
        (plan_key,),
    ).fetchone()
    if existing is not None:
        if (*existing[:3], *existing[4:]) != (*values[:3], *values[4:]):
            raise ProtectiveOcoPlanConflictError
        return False
    identities: list[tuple[str, str, str, str, str]] = connection.execute(
        """SELECT parent_intent_id, symbol, side, take_profit_limit, stop_price
        FROM protective_oco_plans WHERE client_order_id = ?""",
        (plan.client_order_id,),
    ).fetchall()
    expected_identity = (
        plan.parent_intent_id,
        plan.symbol,
        plan.side.value,
        str(plan.take_profit_limit),
        str(plan.stop_price),
    )
    if any(identity != expected_identity for identity in identities):
        raise ProtectiveOcoPlanConflictError
    _ = connection.execute(
        "INSERT INTO protective_oco_plans VALUES (" + ",".join("?" for _ in range(11)) + ")",
        values,
    )
    connection.commit()
    return True


def read_protective_oco_plans(
    connection: sqlite3.Connection,
) -> tuple[StoredProtectiveOcoPlan, ...]:
    rows: list[ProtectiveOcoPlanValues] = connection.execute(
        "SELECT * FROM protective_oco_plans ORDER BY rowid"
    ).fetchall()
    return tuple(_stored_plan(row) for row in rows)


def protective_oco_plan_key(plan: ProtectiveOcoExitPlan) -> ProtectiveOcoPlanKey:
    material = json.dumps(
        (
            plan.client_order_id,
            plan.parent_intent_id,
            plan.symbol,
            plan.side.value,
            plan.quantity,
            str(plan.take_profit_limit),
            str(plan.stop_price),
            plan.time_in_force,
            plan.extended_hours,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return ProtectiveOcoPlanKey(hashlib.sha256(material.encode()).hexdigest())


def protective_oco_snapshot_matches_plan(
    snapshot: ProtectiveOcoSnapshot,
    plan: ProtectiveOcoExitPlan,
) -> bool:
    take_profit = snapshot.take_profit
    stop_loss = snapshot.stop_loss
    return (
        take_profit.client_order_id == plan.client_order_id
        and take_profit.symbol == plan.symbol
        and stop_loss.symbol == plan.symbol
        and take_profit.side is plan.side
        and stop_loss.side is plan.side
        and take_profit.quantity == plan.quantity
        and take_profit.limit_price == plan.take_profit_limit
        and stop_loss.stop_price == plan.stop_price
    )


def _plan_values(
    plan_key: ProtectiveOcoPlanKey,
    plan: ProtectiveOcoExitPlan,
    planned_at: dt.datetime,
) -> ProtectiveOcoPlanValues:
    return (
        plan_key,
        plan.parent_intent_id,
        plan.client_order_id,
        planned_at.isoformat(),
        plan.symbol,
        plan.side.value,
        plan.quantity,
        str(plan.take_profit_limit),
        str(plan.stop_price),
        plan.time_in_force,
        int(plan.extended_hours),
    )


def _stored_plan(row: ProtectiveOcoPlanValues) -> StoredProtectiveOcoPlan:
    return StoredProtectiveOcoPlan(
        ProtectiveOcoPlanKey(row[0]),
        row[3],
        ProtectiveOcoExitPlan(
            client_order_id=ProtectiveOcoClientOrderId(row[2]),
            parent_intent_id=IntentId(row[1]),
            symbol=row[4],
            side=PaperOrderSide(row[5]),
            quantity=row[6],
            take_profit_limit=Decimal(row[7]),
            stop_price=Decimal(row[8]),
        ),
    )


def _require_plan(
    plan: ProtectiveOcoExitPlan,
    planned_at: dt.datetime,
) -> None:
    if (
        planned_at.tzinfo is None
        or planned_at.utcoffset() is None
        or not plan.client_order_id
        or len(plan.client_order_id) > 48
        or not plan.parent_intent_id
        or not plan.symbol
        or plan.quantity <= 0
        or not plan.take_profit_limit.is_finite()
        or not plan.stop_price.is_finite()
        or min(plan.take_profit_limit, plan.stop_price) <= 0
    ):
        raise InvalidProtectiveOcoPlanError
