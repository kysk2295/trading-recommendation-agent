from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import final

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicTerminalEvidence,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_sqlite import (
    AlpacaSipDynamicReceiptWriter,
    require_dynamic_receipt_schema,
    require_private_dynamic_receipt_file,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    dynamic_subscription_request_bytes,
)

type _TerminalRow = tuple[str, str, str, str, int, str]


@final
class AlpacaSipDynamicTerminalStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(
        self,
        plan: AlpacaSipDynamicSubscriptionPlan,
        connection_epoch: str,
        terminal_at: dt.datetime,
        status: AlpacaSipDynamicTerminalStatus,
    ) -> AlpacaSipDynamicTerminalEvidence:
        try:
            _ = dynamic_subscription_request_bytes(plan)
            replay = AlpacaSipDynamicReceiptStore(self.path).load_replay(plan, connection_epoch)
            evidence = _evidence(plan, connection_epoch, terminal_at, status, tuple(item.receipt_id for item in replay))
            _validate_time(evidence, plan, replay[-1].received_at if replay else None)
            row = _row(evidence)
            with AlpacaSipDynamicReceiptWriter(self.path) as connection:
                binding: tuple[str] | None = connection.execute(
                    "SELECT plan_id FROM dynamic_connections WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
                current_ids = tuple(
                    item[0]
                    for item in connection.execute(
                        "SELECT receipt_id FROM dynamic_receipts WHERE connection_epoch=? ORDER BY sequence",
                        (connection_epoch,),
                    ).fetchall()
                )
                if binding != (plan.plan_id,) or current_ids != evidence.receipt_ids:
                    raise AlpacaSipDynamicReceiptError
                existing: _TerminalRow | None = connection.execute(
                    "SELECT connection_epoch,plan_id,terminal_at,status,receipt_count,content_sha256 "
                    "FROM dynamic_terminals WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipDynamicReceiptError
                    return evidence
                _ = connection.execute("INSERT INTO dynamic_terminals VALUES (?,?,?,?,?,?)", row)
                connection.commit()
            return evidence
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipDynamicReceiptError from None

    def load(
        self,
        plan: AlpacaSipDynamicSubscriptionPlan,
        connection_epoch: str,
    ) -> AlpacaSipDynamicTerminalEvidence | None:
        try:
            _ = dynamic_subscription_request_bytes(plan)
            replay = AlpacaSipDynamicReceiptStore(self.path).load_replay(plan, connection_epoch)
            require_private_dynamic_receipt_file(self.path)
            if not self.path.exists():
                return None
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_dynamic_receipt_schema(connection)
                if connection.execute("PRAGMA user_version").fetchone() == (1,):
                    return None
                binding: tuple[str] | None = connection.execute(
                    "SELECT plan_id FROM dynamic_connections WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
                row: _TerminalRow | None = connection.execute(
                    "SELECT connection_epoch,plan_id,terminal_at,status,receipt_count,content_sha256 "
                    "FROM dynamic_terminals WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
            if row is None:
                return None
            receipt_ids = tuple(item.receipt_id for item in replay)
            evidence = _evidence(
                plan,
                connection_epoch,
                dt.datetime.fromisoformat(row[2]),
                AlpacaSipDynamicTerminalStatus(row[3]),
                receipt_ids,
            )
            _validate_time(evidence, plan, replay[-1].received_at if replay else None)
            if binding != (plan.plan_id,) or tuple(row) != _row(evidence):
                raise AlpacaSipDynamicReceiptError
            return evidence
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipDynamicReceiptError from None


def _evidence(
    plan: AlpacaSipDynamicSubscriptionPlan,
    connection_epoch: str,
    terminal_at: dt.datetime,
    status: AlpacaSipDynamicTerminalStatus,
    receipt_ids: tuple[str, ...],
) -> AlpacaSipDynamicTerminalEvidence:
    if type(terminal_at) is not dt.datetime or terminal_at.tzinfo is None or terminal_at.utcoffset() is None:
        raise AlpacaSipDynamicReceiptError
    values = (
        connection_epoch,
        plan.plan_id,
        terminal_at.astimezone(dt.UTC).isoformat(),
        status.value,
        *receipt_ids,
    )
    content_sha256 = hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return AlpacaSipDynamicTerminalEvidence(
        plan.plan_id,
        connection_epoch,
        terminal_at.astimezone(dt.UTC),
        status,
        receipt_ids,
        content_sha256,
    )


def _row(evidence: AlpacaSipDynamicTerminalEvidence) -> _TerminalRow:
    return (
        evidence.connection_epoch,
        evidence.plan_id,
        evidence.terminal_at.isoformat(),
        evidence.status.value,
        evidence.receipt_count,
        evidence.content_sha256,
    )


def _validate_time(
    evidence: AlpacaSipDynamicTerminalEvidence,
    plan: AlpacaSipDynamicSubscriptionPlan,
    last_received_at: dt.datetime | None,
) -> None:
    earliest = plan.evaluated_at if last_received_at is None else last_received_at
    if evidence.terminal_at < earliest:
        raise AlpacaSipDynamicReceiptError


__all__ = ("AlpacaSipDynamicTerminalStore",)
