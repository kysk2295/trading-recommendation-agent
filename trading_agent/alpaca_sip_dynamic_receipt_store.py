from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import final

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicReceiptKind,
    StoredAlpacaSipDynamicReceipt,
)
from trading_agent.alpaca_sip_dynamic_receipt_sqlite import (
    AlpacaSipDynamicReceiptWriter,
    require_dynamic_receipt_schema,
    require_private_dynamic_receipt_file,
)
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    dynamic_subscription_request_bytes,
)

_EPOCH = re.compile(r"^[0-9a-f]{32}$")
type _BindingRow = tuple[str, str, str, str, str, str, str, str, str]
type _ReceiptRow = tuple[int, str, str, int, str, str, str, str, bytes]


@final
class AlpacaSipDynamicReceiptStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def bind_connection(
        self,
        connection_epoch: str,
        plan: AlpacaSipDynamicSubscriptionPlan,
        bound_at: dt.datetime,
    ) -> None:
        try:
            _validate_plan(plan)
            row = _binding_row(connection_epoch, plan, bound_at)
            with AlpacaSipDynamicReceiptWriter(self.path) as connection:
                existing: _BindingRow | None = connection.execute(
                    "SELECT connection_epoch,plan_id,policy_identity_sha256,policy_semantic_version,"
                    "evaluated_at,market_date,bindings_json,bound_at,content_sha256 "
                    "FROM dynamic_connections WHERE connection_epoch=?",
                    (connection_epoch,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipDynamicReceiptError
                    return
                _ = connection.execute("INSERT INTO dynamic_connections VALUES (?,?,?,?,?,?,?,?,?)", row)
                connection.commit()
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipDynamicReceiptError from None

    def append_raw(
        self,
        plan: AlpacaSipDynamicSubscriptionPlan,
        receipt: AlpacaSipDynamicRawReceipt,
    ) -> StoredAlpacaSipDynamicReceipt:
        try:
            _validate_plan(plan)
            if type(receipt) is not AlpacaSipDynamicRawReceipt:
                raise AlpacaSipDynamicReceiptError
            with AlpacaSipDynamicReceiptWriter(self.path) as connection:
                bound_at = _load_binding(connection, receipt.connection_epoch, plan)
                if bound_at is None or receipt.received_at < bound_at:
                    raise AlpacaSipDynamicReceiptError
                existing: _ReceiptRow | None = connection.execute(
                    "SELECT generation,receipt_id,connection_epoch,sequence,plan_id,kind,received_at,"
                    "payload_sha256,payload FROM dynamic_receipts "
                    "WHERE connection_epoch=? AND sequence=?",
                    (receipt.connection_epoch, receipt.sequence),
                ).fetchone()
                if existing is not None:
                    stored = _stored(existing)
                    if tuple(existing[1:]) != _receipt_values(plan, receipt):
                        raise AlpacaSipDynamicReceiptError
                    return stored
                latest: tuple[int] = connection.execute(
                    "SELECT coalesce(max(sequence),0) FROM dynamic_receipts WHERE connection_epoch=?",
                    (receipt.connection_epoch,),
                ).fetchone()
                if receipt.sequence != latest[0] + 1:
                    raise AlpacaSipDynamicReceiptError
                row = _receipt_values(plan, receipt)
                cursor = connection.execute(
                    "INSERT INTO dynamic_receipts "
                    "(receipt_id,connection_epoch,sequence,plan_id,kind,received_at,payload_sha256,payload) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
                generation = cursor.lastrowid
                if type(generation) is not int:
                    raise AlpacaSipDynamicReceiptError
                return _stored((generation, *row))
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipDynamicReceiptError from None

    def load_replay(
        self,
        plan: AlpacaSipDynamicSubscriptionPlan,
        connection_epoch: str,
    ) -> tuple[StoredAlpacaSipDynamicReceipt, ...]:
        try:
            _validate_plan(plan)
            if _EPOCH.fullmatch(connection_epoch) is None:
                raise AlpacaSipDynamicReceiptError
            require_private_dynamic_receipt_file(self.path)
            if not self.path.exists():
                return ()
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_dynamic_receipt_schema(connection)
                bound_at = _load_binding(connection, connection_epoch, plan)
                if bound_at is None:
                    return ()
                rows: list[_ReceiptRow] = connection.execute(
                    "SELECT generation,receipt_id,connection_epoch,sequence,plan_id,kind,received_at,"
                    "payload_sha256,payload FROM dynamic_receipts "
                    "WHERE connection_epoch=? ORDER BY sequence",
                    (connection_epoch,),
                ).fetchall()
            replay = tuple(_stored(row) for row in rows)
            if tuple(item.sequence for item in replay) != tuple(range(1, len(replay) + 1)) or any(
                item.plan_id != plan.plan_id or item.received_at < bound_at for item in replay
            ):
                raise AlpacaSipDynamicReceiptError
            return replay
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipDynamicReceiptError from None


def _load_binding(
    connection: sqlite3.Connection,
    connection_epoch: str,
    plan: AlpacaSipDynamicSubscriptionPlan,
) -> dt.datetime | None:
    row: _BindingRow | None = connection.execute(
        "SELECT connection_epoch,plan_id,policy_identity_sha256,policy_semantic_version,evaluated_at,"
        "market_date,bindings_json,bound_at,content_sha256 FROM dynamic_connections "
        "WHERE connection_epoch=?",
        (connection_epoch,),
    ).fetchone()
    if row is None:
        return None
    bound_at = dt.datetime.fromisoformat(row[7])
    expected = _binding_row(connection_epoch, plan, bound_at)
    if tuple(row) != expected:
        raise AlpacaSipDynamicReceiptError
    return bound_at


def _binding_row(
    connection_epoch: str,
    plan: AlpacaSipDynamicSubscriptionPlan,
    bound_at: dt.datetime,
) -> _BindingRow:
    if _EPOCH.fullmatch(connection_epoch) is None or not _aware(bound_at) or bound_at < plan.evaluated_at:
        raise AlpacaSipDynamicReceiptError
    values = (
        connection_epoch,
        plan.plan_id,
        plan.policy_identity_sha256,
        plan.policy_semantic_version,
        plan.evaluated_at.isoformat(),
        plan.market_date.isoformat(),
        json.dumps(
            [{"instrument_id": item.instrument_id, "symbol": item.symbol} for item in plan.bindings],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        bound_at.astimezone(dt.UTC).isoformat(),
    )
    return (*values, _hash(values))


def _receipt_values(
    plan: AlpacaSipDynamicSubscriptionPlan,
    receipt: AlpacaSipDynamicRawReceipt,
) -> tuple[str, str, int, str, str, str, str, bytes]:
    payload_sha256 = hashlib.sha256(receipt.payload).hexdigest()
    receipt_id = _receipt_id(plan.plan_id, receipt, payload_sha256)
    return (
        receipt_id,
        receipt.connection_epoch,
        receipt.sequence,
        plan.plan_id,
        receipt.kind.value,
        receipt.received_at.astimezone(dt.UTC).isoformat(),
        payload_sha256,
        receipt.payload,
    )


def _receipt_id(
    plan_id: str,
    receipt: AlpacaSipDynamicRawReceipt,
    payload_sha256: str,
) -> str:
    identity = (
        plan_id,
        receipt.connection_epoch,
        receipt.sequence,
        receipt.received_at.astimezone(dt.UTC).isoformat(),
        receipt.kind.value,
        payload_sha256,
    )
    return _hash(identity)


def _stored(row: _ReceiptRow) -> StoredAlpacaSipDynamicReceipt:
    stored = StoredAlpacaSipDynamicReceipt(
        row[0],
        row[1],
        row[4],
        row[2],
        row[3],
        dt.datetime.fromisoformat(row[6]),
        AlpacaSipDynamicReceiptKind(row[5]),
        row[7],
        row[8],
    )
    raw = AlpacaSipDynamicRawReceipt(
        stored.connection_epoch,
        stored.sequence,
        stored.received_at,
        stored.kind,
        stored.payload,
    )
    payload_sha256 = hashlib.sha256(stored.payload).hexdigest()
    if stored.payload_sha256 != payload_sha256 or stored.receipt_id != _receipt_id(
        stored.plan_id,
        raw,
        payload_sha256,
    ):
        raise AlpacaSipDynamicReceiptError
    return stored


def _validate_plan(plan: AlpacaSipDynamicSubscriptionPlan) -> None:
    _ = dynamic_subscription_request_bytes(plan)


def _hash(values: tuple[str | int, ...]) -> str:
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = ("AlpacaSipDynamicReceiptStore",)
