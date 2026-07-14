from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import NewType, override

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    PaperOrderSide,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.us_equity_calendar import NEW_YORK

PaperSafetyPlanKey = NewType("PaperSafetyPlanKey", str)

type SafetyPlanRow = tuple[str, str, str, str, str, str, str, str]
type SafetyActionRow = tuple[str, int, str, str | None, str, int | None, str | None, str | None]


class InvalidPaperSafetyPlanError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Paper 안전조치 계획이 실행 원장 계약과 일치하지 않습니다"


class PaperSafetyPlanConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 Paper 안전조치 계획 key의 immutable 값이 다릅니다"


@dataclass(frozen=True, slots=True)
class StoredPaperSafetyPlan:
    plan_key: PaperSafetyPlanKey
    actions_sha256: str
    plan: PaperSafetyPlan


def save_paper_safety_plan(
    connection: sqlite3.Connection,
    plan: PaperSafetyPlan,
) -> bool:
    _require_plan(plan)
    action_values = tuple(_action_values(index, action) for index, action in enumerate(plan.actions))
    actions_hash = _actions_sha256(plan.actions)
    plan_key = paper_safety_plan_key(plan, actions_hash)
    values = _plan_values(plan_key, plan, actions_hash)
    existing: SafetyPlanRow | None = connection.execute(
        "SELECT * FROM paper_safety_plans WHERE plan_key = ?",
        (plan_key,),
    ).fetchone()
    if existing is not None:
        stored = _stored_plan(existing, _read_actions(connection, plan_key))
        if stored.plan != plan or stored.actions_sha256 != actions_hash:
            raise PaperSafetyPlanConflictError
        return False
    with connection:
        _ = connection.execute(
            "INSERT INTO paper_safety_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        connection.executemany(
            "INSERT INTO paper_safety_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ((plan_key, *action) for action in action_values),
        )
    return True


def read_paper_safety_plans(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperSafetyPlan, ...]:
    rows: list[SafetyPlanRow] = connection.execute("SELECT * FROM paper_safety_plans ORDER BY rowid").fetchall()
    return tuple(_stored_plan(row, _read_actions(connection, PaperSafetyPlanKey(row[0]))) for row in rows)


def paper_safety_plan_key(
    plan: PaperSafetyPlan,
    actions_sha256: str | None = None,
) -> PaperSafetyPlanKey:
    material = json.dumps(
        (
            plan.account_fingerprint,
            plan.observed_at.isoformat(),
            plan.session_date.isoformat(),
            plan.phase.value,
            str(plan.mark_to_market_daily_pnl),
            str(plan.conservative_daily_pnl),
            _actions_sha256(plan.actions) if actions_sha256 is None else actions_sha256,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return PaperSafetyPlanKey(hashlib.sha256(material.encode()).hexdigest())


def _plan_values(
    plan_key: PaperSafetyPlanKey,
    plan: PaperSafetyPlan,
    actions_sha256: str,
) -> SafetyPlanRow:
    return (
        plan_key,
        plan.account_fingerprint,
        plan.observed_at.isoformat(),
        plan.session_date.isoformat(),
        plan.phase.value,
        str(plan.mark_to_market_daily_pnl),
        str(plan.conservative_daily_pnl),
        actions_sha256,
    )


def _action_values(
    sequence: int,
    action: PaperSafetyAction,
) -> tuple[int, str, str | None, str, int | None, str | None, str | None]:
    if isinstance(action, PaperCancelOrderAction):
        return (
            sequence,
            action.kind,
            action.broker_order_id,
            action.symbol,
            int(action.protective_oco),
            None,
            None,
        )
    return sequence, action.kind, None, action.symbol, None, action.side.value, str(action.quantity)


def _read_actions(
    connection: sqlite3.Connection,
    plan_key: PaperSafetyPlanKey,
) -> tuple[SafetyActionRow, ...]:
    return tuple(
        connection.execute(
            "SELECT * FROM paper_safety_actions WHERE plan_key = ? ORDER BY sequence",
            (plan_key,),
        ).fetchall()
    )


def _stored_plan(
    row: SafetyPlanRow,
    action_rows: tuple[SafetyActionRow, ...],
) -> StoredPaperSafetyPlan:
    actions = tuple(_stored_action(action) for action in action_rows)
    if _actions_sha256(actions) != row[7]:
        raise InvalidPaperSafetyPlanError
    plan = PaperSafetyPlan(
        AccountFingerprint(row[1]),
        _aware_datetime(row[2]),
        dt.date.fromisoformat(row[3]),
        PaperSafetyPhase(row[4]),
        Decimal(row[5]),
        Decimal(row[6]),
        actions,
    )
    _require_plan(plan)
    if paper_safety_plan_key(plan, row[7]) != row[0]:
        raise InvalidPaperSafetyPlanError
    return StoredPaperSafetyPlan(PaperSafetyPlanKey(row[0]), row[7], plan)


def _stored_action(row: SafetyActionRow) -> PaperSafetyAction:
    if row[2] == "cancel_order" and row[3] is not None and row[5] is not None:
        return PaperCancelOrderAction(BrokerOrderId(row[3]), row[4], bool(row[5]))
    if row[2] == "close_position" and row[6] is not None and row[7] is not None:
        return PaperClosePositionAction(row[4], PaperOrderSide(row[6]), Decimal(row[7]))
    raise InvalidPaperSafetyPlanError


def _actions_sha256(actions: tuple[PaperSafetyAction, ...]) -> str:
    material = json.dumps(
        tuple(_action_values(index, action) for index, action in enumerate(actions)),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _require_plan(plan: PaperSafetyPlan) -> None:
    cancel_ids = tuple(action.broker_order_id for action in plan.actions if isinstance(action, PaperCancelOrderAction))
    close_symbols = tuple(action.symbol for action in plan.actions if isinstance(action, PaperClosePositionAction))
    if (
        len(plan.account_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in plan.account_fingerprint)
        or plan.observed_at.tzinfo is None
        or plan.observed_at.utcoffset() is None
        or plan.observed_at.astimezone(NEW_YORK).date() != plan.session_date
        or plan.phase is PaperSafetyPhase.MONITORING
        or not plan.mark_to_market_daily_pnl.is_finite()
        or not plan.conservative_daily_pnl.is_finite()
        or len(cancel_ids) != len(set(cancel_ids))
        or len(close_symbols) != len(set(close_symbols))
        or not _actions_are_ordered(plan)
        or any(not _valid_action(action) for action in plan.actions)
    ):
        raise InvalidPaperSafetyPlanError


def _valid_action(action: PaperSafetyAction) -> bool:
    if not action.symbol or action.symbol != action.symbol.upper():
        return False
    if isinstance(action, PaperCancelOrderAction):
        return bool(action.broker_order_id)
    return (
        action.quantity.is_finite() and action.quantity > 0 and action.quantity == action.quantity.to_integral_value()
    )


def _actions_are_ordered(plan: PaperSafetyPlan) -> bool:
    ranks = tuple(
        0
        if isinstance(action, PaperCancelOrderAction) and not action.protective_oco
        else 1
        if isinstance(action, PaperCancelOrderAction)
        else 2
        for action in plan.actions
    )
    if ranks != tuple(sorted(ranks)):
        return False
    if plan.phase is PaperSafetyPhase.ENTRY_CUTOFF:
        return all(rank == 0 for rank in ranks)
    return True


def _aware_datetime(value: str) -> dt.datetime:

    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidPaperSafetyPlanError
    return parsed
