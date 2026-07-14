from __future__ import annotations

import sqlite3

from trading_agent.execution_schema import IntentRow, intent_values
from trading_agent.execution_store_errors import IntentConflictError
from trading_agent.paper_execution_models import PaperOrderIntent, SizedPaperOrder
from trading_agent.paper_mutation_ledger_models import PaperMutationIntent
from trading_agent.paper_mutation_store import save_paper_mutation_intent


def save_order_intent(
    connection: sqlite3.Connection,
    intent: PaperOrderIntent,
    quantity: int,
) -> bool:
    values = intent_values(intent, quantity)
    existing: IntentRow | None = connection.execute(
        "SELECT * FROM order_intents WHERE intent_id = ?",
        (intent.intent_id,),
    ).fetchone()
    if existing is not None:
        if existing != values:
            raise IntentConflictError(intent.intent_id)
        return False
    _ = connection.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    connection.commit()
    return True


def save_entry_mutation_intent(
    connection: sqlite3.Connection,
    order: SizedPaperOrder,
    mutation: PaperMutationIntent,
) -> bool:
    values = intent_values(order.intent, order.quantity)
    with connection:
        existing: IntentRow | None = connection.execute(
            "SELECT * FROM order_intents WHERE intent_id = ?",
            (order.intent.intent_id,),
        ).fetchone()
        if existing is not None and existing != values:
            raise IntentConflictError(order.intent.intent_id)
        order_inserted = existing is None
        if order_inserted:
            _ = connection.execute(
                "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        mutation_inserted = save_paper_mutation_intent(
            connection,
            mutation,
            commit=False,
        )
    return order_inserted or mutation_inserted
