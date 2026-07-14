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
