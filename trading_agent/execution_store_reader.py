from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_database import require_current_execution_schema
from trading_agent.execution_ledger_identity import (
    ExecutionLedgerSnapshotIdentity,
    read_execution_ledger_snapshot_identity,
)
from trading_agent.execution_ledger_reader import (
    ReconciliationLedger,
    read_reconciliation_ledger,
)
from trading_agent.execution_schema import (
    SCHEMA_VERSION,
    BrokerEventRow,
    StoredAccountBinding,
    StoredBrokerEvent,
    StoredIntent,
    stored_broker_event,
    stored_intent,
)
from trading_agent.execution_store_errors import InvalidExecutionLedgerGenerationError
from trading_agent.paper_account_activity_store import (
    StoredPaperAccountActivity,
    read_paper_account_activities,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
)
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
    read_paper_mutation_events,
    read_paper_mutation_intents,
)
from trading_agent.paper_protective_oco_store import (
    StoredProtectiveOcoPlan,
    read_protective_oco_plans,
)
from trading_agent.paper_safety_store import (
    StoredPaperSafetyPlan,
    read_paper_safety_plans,
)
from trading_agent.paper_stream_recovery import (
    StoredPaperRecoveryOrder,
    StoredPaperStreamRecovery,
    StoredProtectiveOcoSnapshot,
    read_paper_recovery_orders,
    read_paper_recovery_protective_ocos,
    read_paper_stream_recoveries,
)
from trading_agent.trade_update_receipts import (
    StoredTradeUpdateReceipt,
    StoredTradeUpdateReceiptDisposition,
    TradeUpdateReceiptKey,
    pending_trade_update_receipt_keys,
    read_trade_update_receipt_dispositions,
    read_trade_update_receipts,
)
from trading_agent.trade_update_schema import StoredTradeUpdate
from trading_agent.trade_update_store import read_trade_updates


class ExecutionStoreReader:
    __slots__ = ()

    path: Path

    def intents(self) -> tuple[StoredIntent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows = connection.execute("SELECT * FROM order_intents ORDER BY created_at, intent_id").fetchall()
        return tuple(stored_intent(row) for row in rows)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            row: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        return row == (SCHEMA_VERSION,)

    def unresolved_intent_ids(self) -> frozenset[IntentId]:
        return self.reconciliation_ledger().unresolved_intent_ids

    def account_fingerprint(self) -> AccountFingerprint | None:
        binding = self.account_binding()
        return None if binding is None else binding.account_fingerprint

    def account_binding(self) -> StoredAccountBinding | None:
        if not self.path.is_file():
            return None
        with self._reader_connection() as connection:
            row: tuple[str, str] | None = connection.execute(
                "SELECT account_fingerprint, bound_at FROM account_binding WHERE binding_id = 1"
            ).fetchone()
        return None if row is None else StoredAccountBinding(AccountFingerprint(row[0]), row[1])

    def ledger_snapshot_identity(self) -> ExecutionLedgerSnapshotIdentity:
        if not self.path.is_file():
            raise InvalidExecutionLedgerGenerationError
        with self._reader_connection() as connection:
            _ = connection.execute("BEGIN")
            return read_execution_ledger_snapshot_identity(connection)

    def broker_events(self, intent_id: IntentId) -> tuple[StoredBrokerEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[BrokerEventRow] = connection.execute(
                """SELECT event_id, event_key, intent_id, occurred_at, event_type,
                broker_order_id, payload_json FROM broker_order_events
                WHERE intent_id = ? ORDER BY event_id""",
                (intent_id,),
            ).fetchall()
        return tuple(stored_broker_event(row) for row in rows)

    def trade_updates(self, intent_id: IntentId) -> tuple[StoredTradeUpdate, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_trade_updates(connection, intent_id)

    def trade_update_receipts(self) -> tuple[StoredTradeUpdateReceipt, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_trade_update_receipts(connection)

    def trade_update_receipt_dispositions(
        self,
    ) -> tuple[StoredTradeUpdateReceiptDisposition, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_trade_update_receipt_dispositions(connection)

    def pending_trade_update_receipt_keys(
        self,
    ) -> frozenset[TradeUpdateReceiptKey]:
        if not self.path.is_file():
            return frozenset()
        with self._reader_connection() as connection:
            return pending_trade_update_receipt_keys(connection)

    def paper_stream_recoveries(self) -> tuple[StoredPaperStreamRecovery, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_stream_recoveries(connection)

    def paper_recovery_orders(self) -> tuple[StoredPaperRecoveryOrder, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_recovery_orders(connection)

    def paper_account_activities(self) -> tuple[StoredPaperAccountActivity, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_account_activities(connection)

    def protective_oco_plans(self) -> tuple[StoredProtectiveOcoPlan, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_protective_oco_plans(connection)

    def paper_recovery_protective_ocos(
        self,
    ) -> tuple[StoredProtectiveOcoSnapshot, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_recovery_protective_ocos(connection)

    def paper_safety_plans(self) -> tuple[StoredPaperSafetyPlan, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_safety_plans(connection)

    def paper_mutation_intents(self) -> tuple[StoredPaperMutationIntent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_mutation_intents(connection)

    def paper_mutation_events(self) -> tuple[StoredPaperMutationEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            return read_paper_mutation_events(connection)

    def reconciliation_ledger(self) -> ReconciliationLedger:
        return read_reconciliation_ledger(self.path)

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        require_current_execution_schema(connection, self.path)
        return connection
