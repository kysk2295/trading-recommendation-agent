from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final

from trading_agent.alpaca_paper_order_stream import PaperTradeUpdateFrame
from trading_agent.alpaca_trade_updates import AlpacaTradeUpdate
from trading_agent.execution_database import prepare_execution_writer_connection
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    UnboundExecutionAccountError,
)
from trading_agent.execution_schema import (
    AccountBindingRow,
    BrokerEventValues,
    IntentRow,
    broker_event_values,
    intent_values,
)
from trading_agent.execution_store_errors import (
    BrokerEventConflictError,
    InactiveExecutionWriterError,
    IntentConflictError,
    WriterLeaseUnavailableError,
)
from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
    BrokerOrderEvent,
    PaperOrderIntent,
)
from trading_agent.paper_stream_recovery import (
    PaperStreamRecoveryObservation,
    append_paper_stream_recovery,
)
from trading_agent.trade_update_receipts import (
    StoredTradeUpdateReceipt,
    TradeUpdateReceiptDisposition,
    TradeUpdateReceiptKey,
    TradeUpdateReceiptReason,
    classify_trade_update_receipt,
    save_trade_update_receipt,
)
from trading_agent.trade_update_store import append_trade_update


@final
class ExecutionWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def save_intent(self, intent: PaperOrderIntent, quantity: int) -> bool:
        self._require_active()
        values = intent_values(intent, quantity)
        existing: IntentRow | None = self._connection.execute(
            "SELECT * FROM order_intents WHERE intent_id = ?",
            (intent.intent_id,),
        ).fetchone()
        if existing is not None:
            if existing != values:
                raise IntentConflictError(intent.intent_id)
            return False
        _ = self._connection.execute(
            "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        self._connection.commit()
        return True

    def bind_account(
        self,
        account_fingerprint: AccountFingerprint,
        bound_at: dt.datetime,
    ) -> bool:
        self._require_active()
        existing: AccountBindingRow | None = self._connection.execute(
            "SELECT account_fingerprint, bound_at FROM account_binding WHERE binding_id = 1"
        ).fetchone()
        if existing is not None:
            if existing[0] != account_fingerprint:
                raise AccountBindingConflictError
            return False
        _ = self._connection.execute(
            """INSERT INTO account_binding
            (binding_id, account_fingerprint, bound_at) VALUES (1, ?, ?)""",
            (account_fingerprint, bound_at.isoformat()),
        )
        self._connection.commit()
        return True

    def append_broker_event(
        self,
        event: BrokerOrderEvent,
        *,
        account_fingerprint: AccountFingerprint,
    ) -> bool:
        self._require_active()
        self._require_bound_account(account_fingerprint)
        values = broker_event_values(event)
        existing: BrokerEventValues | None = (
            self._connection.execute(
                """SELECT event_key, intent_id, occurred_at, event_type,
                broker_order_id, payload_json FROM broker_order_events
                WHERE event_key = ?""",
                (event.event_key,),
            ).fetchone()
        )
        if existing is not None:
            if existing != values:
                raise BrokerEventConflictError(event.event_key)
            return False
        _ = self._connection.execute(
            """INSERT INTO broker_order_events
            (event_key, intent_id, occurred_at, event_type, broker_order_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            values,
        )
        self._connection.commit()
        return True

    def _require_bound_account(
        self,
        account_fingerprint: AccountFingerprint,
    ) -> None:
        row: tuple[str] | None = self._connection.execute(
            "SELECT account_fingerprint FROM account_binding WHERE binding_id = 1"
        ).fetchone()
        if row is None:
            raise UnboundExecutionAccountError
        if row[0] != account_fingerprint:
            raise AccountBindingConflictError

    def append_trade_update(
        self,
        update: AlpacaTradeUpdate,
        *,
        account_fingerprint: AccountFingerprint,
        connection_epoch: str,
        received_at: dt.datetime,
    ) -> bool:
        self._require_active()
        return append_trade_update(
            self._connection,
            update,
            account_fingerprint=account_fingerprint,
            connection_epoch=connection_epoch,
            received_at=received_at,
        )

    def save_trade_update_receipt(
        self,
        frame: PaperTradeUpdateFrame,
        *,
        account_fingerprint: AccountFingerprint,
        connection_epoch: str,
        received_at: dt.datetime,
    ) -> StoredTradeUpdateReceipt:
        self._require_active()
        self._require_bound_account(account_fingerprint)
        return save_trade_update_receipt(
            self._connection,
            frame,
            account_fingerprint=account_fingerprint,
            connection_epoch=connection_epoch,
            received_at=received_at,
        )

    def accept_trade_update_receipt(
        self,
        receipt_key: TradeUpdateReceiptKey,
        event_key: BrokerEventKey,
        *,
        classified_at: dt.datetime,
    ) -> bool:
        self._require_active()
        return classify_trade_update_receipt(
            self._connection,
            receipt_key,
            disposition=TradeUpdateReceiptDisposition.ACCEPTED,
            event_key=event_key,
            reason=None,
            classified_at=classified_at,
        )

    def quarantine_trade_update_receipt(
        self,
        receipt_key: TradeUpdateReceiptKey,
        *,
        reason: TradeUpdateReceiptReason,
        classified_at: dt.datetime,
    ) -> bool:
        self._require_active()
        return classify_trade_update_receipt(
            self._connection,
            receipt_key,
            disposition=TradeUpdateReceiptDisposition.QUARANTINED,
            event_key=None,
            reason=reason,
            classified_at=classified_at,
        )

    def append_paper_stream_recovery(
        self,
        observation: PaperStreamRecoveryObservation,
    ) -> bool:
        self._require_active()
        self._require_bound_account(observation.account_fingerprint)
        return append_paper_stream_recovery(self._connection, observation)

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveExecutionWriterError

    def _close(self) -> None:
        self._active = False
        self._connection.close()


@final
class ExecutionStore(ExecutionStoreReader):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    @contextmanager
    def writer(self) -> Iterator[ExecutionWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WriterLeaseUnavailableError(lock_path) from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            prepare_execution_writer_connection(connection, self.path)
            writer = ExecutionWriter(connection)
            try:
                yield writer
            finally:
                writer._close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
