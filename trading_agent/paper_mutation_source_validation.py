from __future__ import annotations

import sqlite3

from trading_agent.paper_mutation_ledger_models import (
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_validation import (
    InvalidPaperMutationRecordError,
)


def require_mutation_source(
    connection: sqlite3.Connection,
    intent: PaperMutationIntent,
) -> None:
    if intent.entry_intent_id is not None:
        entry_row: tuple[str, str, int] | None = connection.execute(
            "SELECT symbol, side, quantity FROM order_intents WHERE intent_id = ?",
            (intent.entry_intent_id,),
        ).fetchone()
        entry_expected = (
            intent.symbol,
            intent.side.value if intent.side else "",
            int(intent.quantity or 0),
        )
        if entry_row != entry_expected:
            raise InvalidPaperMutationRecordError
        return
    if intent.operation is PaperMutationOperation.CANCEL_PROTECTIVE_OCO:
        protective_cancel_row: tuple[str] | None = connection.execute(
            """SELECT plans.symbol
            FROM protective_oco_plans AS plans
            JOIN paper_recovery_protective_oco_legs AS legs
              ON legs.plan_key = plans.plan_key
            WHERE plans.plan_key = ?
              AND legs.parent_broker_order_id = ?
              AND legs.leg_kind = 'take_profit'
            LIMIT 1""",
            (intent.protective_plan_key, intent.broker_order_id),
        ).fetchone()
        if protective_cancel_row != (intent.symbol,):
            raise InvalidPaperMutationRecordError
        return
    if intent.protective_plan_key is not None:
        protective_row: tuple[str, str, int] | None = connection.execute(
            "SELECT symbol, side, quantity FROM protective_oco_plans WHERE plan_key = ?",
            (intent.protective_plan_key,),
        ).fetchone()
        protective_expected = (
            intent.symbol,
            intent.side.value if intent.side else "",
            int(intent.quantity or 0),
        )
        if protective_row != protective_expected:
            raise InvalidPaperMutationRecordError
        return
    safety_row: (
        tuple[
            str,
            str | None,
            str,
            str | None,
            str | None,
        ]
        | None
    ) = connection.execute(
        """SELECT kind, broker_order_id, symbol, side, quantity
        FROM paper_safety_actions WHERE plan_key = ? AND sequence = ?""",
        (intent.safety_plan_key, intent.action_sequence),
    ).fetchone()
    safety_expected = (
        "cancel_order" if intent.operation is PaperMutationOperation.CANCEL_ORDER else "close_position",
        intent.broker_order_id,
        intent.symbol,
        None if intent.side is None else intent.side.value,
        None if intent.quantity is None else str(intent.quantity),
    )
    if safety_row != safety_expected:
        raise InvalidPaperMutationRecordError
