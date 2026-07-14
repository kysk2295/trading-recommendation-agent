from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import override

from trading_agent.paper_execution_models import (
    AccountActivityId,
    AccountFingerprint,
    BrokerOrderId,
    PaperOrderSide,
    PaperTradeActivity,
    PaperTradeActivityType,
)
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryKey

type ActivityValues = tuple[
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
]
type StoredActivityRow = tuple[int, str, *ActivityValues]


class InvalidPaperAccountActivityError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper account activity 증거가 올바르지 않습니다"


class PaperAccountActivityConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 Alpaca paper account activity ID의 immutable 필드가 다릅니다"


@dataclass(frozen=True, slots=True)
class StoredPaperAccountActivity:
    recovery_id: int
    recovery_key: PaperStreamRecoveryKey
    account_fingerprint: AccountFingerprint
    activity: PaperTradeActivity


def append_paper_account_activities(
    connection: sqlite3.Connection,
    recovery_key: PaperStreamRecoveryKey,
    account_fingerprint: AccountFingerprint,
    activities: tuple[PaperTradeActivity, ...],
) -> None:
    activity_ids = tuple(activity.activity_id for activity in activities)
    if len(activity_ids) != len(set(activity_ids)):
        raise InvalidPaperAccountActivityError
    for activity in activities:
        values = _activity_values(account_fingerprint, activity)
        existing: ActivityValues | None = connection.execute(
            "SELECT * FROM paper_account_activities WHERE activity_id = ?",
            (activity.activity_id,),
        ).fetchone()
        if existing is not None and existing != values:
            raise PaperAccountActivityConflictError
        if existing is None:
            _ = connection.execute(
                "INSERT INTO paper_account_activities VALUES (" + ",".join("?" for _ in range(13)) + ")",
                values,
            )
        _ = connection.execute(
            "INSERT OR IGNORE INTO paper_recovery_activities VALUES (?, ?)",
            (recovery_key, activity.activity_id),
        )


def paper_account_activities_sha256(
    activities: tuple[PaperTradeActivity, ...],
) -> str:
    values = tuple(
        sorted(
            (_hash_values(activity) for activity in activities),
            key=lambda item: item[0],
        )
    )
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_paper_account_activities(
    connection: sqlite3.Connection,
) -> tuple[StoredPaperAccountActivity, ...]:
    rows: list[StoredActivityRow] = connection.execute(
        """SELECT recovery.rowid, links.recovery_key, activities.*
        FROM paper_recovery_activities AS links
        JOIN paper_stream_recoveries AS recovery
          ON recovery.recovery_key = links.recovery_key
        JOIN paper_account_activities AS activities
          ON activities.activity_id = links.activity_id
        ORDER BY recovery.rowid, activities.activity_id"""
    ).fetchall()
    stored = tuple(_stored_activity(row) for row in rows)
    recovery_rows: list[tuple[str, str]] = connection.execute(
        "SELECT recovery_key, activities_sha256 FROM paper_stream_recoveries ORDER BY rowid"
    ).fetchall()
    for recovery_key, expected_hash in recovery_rows:
        observed = tuple(item.activity for item in stored if item.recovery_key == recovery_key)
        if paper_account_activities_sha256(observed) != expected_hash:
            raise InvalidPaperAccountActivityError
    return stored


def _activity_values(
    account_fingerprint: AccountFingerprint,
    activity: PaperTradeActivity,
) -> ActivityValues:
    _require_activity(activity)
    payload_hash = hashlib.sha256(activity.payload_json.encode()).hexdigest()
    return (
        activity.activity_id,
        account_fingerprint,
        activity.broker_order_id,
        activity.symbol,
        activity.side.value,
        activity.event_type.value,
        str(activity.quantity),
        str(activity.cumulative_quantity),
        str(activity.leaves_quantity),
        str(activity.price),
        activity.transaction_time.isoformat(),
        activity.payload_json,
        payload_hash,
    )


def _hash_values(activity: PaperTradeActivity) -> tuple[str, ...]:
    values = _activity_values(AccountFingerprint(""), activity)
    return (values[0], *values[2:])


def _stored_activity(row: StoredActivityRow) -> StoredPaperAccountActivity:
    payload_hash = hashlib.sha256(row[13].encode()).hexdigest()
    if payload_hash != row[14]:
        raise InvalidPaperAccountActivityError
    activity = PaperTradeActivity(
        activity_id=AccountActivityId(row[2]),
        broker_order_id=BrokerOrderId(row[4]),
        symbol=row[5],
        side=PaperOrderSide(row[6]),
        event_type=PaperTradeActivityType(row[7]),
        quantity=Decimal(row[8]),
        cumulative_quantity=Decimal(row[9]),
        leaves_quantity=Decimal(row[10]),
        price=Decimal(row[11]),
        transaction_time=dt.datetime.fromisoformat(row[12]),
        payload_json=row[13],
    )
    _require_activity(activity)
    return StoredPaperAccountActivity(
        recovery_id=row[0],
        recovery_key=PaperStreamRecoveryKey(row[1]),
        account_fingerprint=AccountFingerprint(row[3]),
        activity=activity,
    )


def _require_activity(activity: PaperTradeActivity) -> None:
    decimals = (
        activity.quantity,
        activity.cumulative_quantity,
        activity.leaves_quantity,
        activity.price,
    )
    if (
        not activity.activity_id
        or not activity.broker_order_id
        or not activity.symbol
        or activity.symbol.strip() != activity.symbol
        or not activity.payload_json
        or activity.transaction_time.tzinfo is None
        or activity.transaction_time.utcoffset() is None
        or any(not value.is_finite() for value in decimals)
        or activity.quantity <= 0
        or activity.cumulative_quantity < activity.quantity
        or activity.leaves_quantity < 0
        or activity.price <= 0
        or (activity.event_type is PaperTradeActivityType.FILL and activity.leaves_quantity != 0)
        or (activity.event_type is PaperTradeActivityType.PARTIAL_FILL and activity.leaves_quantity <= 0)
    ):
        raise InvalidPaperAccountActivityError
