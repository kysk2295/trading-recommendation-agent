from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryLeaseLostError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryOperationError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import (
    HermesDeliveryAcknowledgement,
    HermesDeliveryAppendResult,
    HermesDeliveryAttempt,
    HermesDeliveryClaim,
    HermesDeliveryEvent,
    HermesDeliveryTransition,
    HermesDeliveryTransitionKind,
    HermesReplyLineage,
    hermes_acknowledgement_id,
    hermes_attempt_id,
    hermes_transition_id,
)
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_schema import (
    CLAIMABLE_HERMES_DELIVERY_SQL,
    prepare_hermes_delivery_schema,
)


class HermesDeliveryStore(HermesDeliveryReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[HermesDeliveryWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise HermesDeliveryWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                prepare_hermes_delivery_schema(connection)
                writer = HermesDeliveryWriter(connection)
                try:
                    yield writer
                finally:
                    writer.close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class HermesDeliveryWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._active = True
        self._connection = connection

    def append_event(self, event: HermesDeliveryEvent) -> HermesDeliveryAppendResult:
        self._require_active()
        event = HermesDeliveryEvent.model_validate(event.model_dump(mode="python"))
        payload = event.model_dump_json()
        existing: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM hermes_delivery_events WHERE delivery_id = ?", (event.delivery_id,)
        ).fetchone()
        if existing is not None:
            if existing != (payload,):
                raise HermesDeliveryConflictError
            return HermesDeliveryAppendResult(delivery_id=event.delivery_id, inserted=False)
        if event.root_delivery_id != event.delivery_id and not self._event_exists(event.root_delivery_id):
            raise HermesDeliveryConflictError
        self._connection.execute(
            "INSERT INTO hermes_delivery_events VALUES (?, ?, ?, ?, ?)",
            (event.delivery_id, event.root_delivery_id, _iso(event.occurred_at), event.max_attempts, payload),
        )
        self._connection.commit()
        return HermesDeliveryAppendResult(delivery_id=event.delivery_id, inserted=True)

    def claim_next(self, *, worker_id: str, now: dt.datetime, lease_seconds: int) -> HermesDeliveryClaim | None:
        self._require_active()
        if lease_seconds < 1 or lease_seconds > 300 or now.tzinfo is None or now.utcoffset() is None:
            raise InvalidHermesDeliveryOperationError("invalid Hermes delivery lease")
        now = now.astimezone(dt.UTC)
        self._connection.execute("BEGIN IMMEDIATE")
        row: tuple[str] | None = self._connection.execute(
            CLAIMABLE_HERMES_DELIVERY_SQL,
            (_iso(now), _iso(now)),
        ).fetchone()
        if row is None:
            self._connection.commit()
            return None
        event = HermesDeliveryEvent.model_validate_json(row[0])
        count: tuple[int] = self._connection.execute(
            "SELECT COUNT(*) FROM hermes_delivery_attempts WHERE delivery_id = ?", (event.delivery_id,)
        ).fetchone() or (0,)
        attempt_number = count[0] + 1
        attempt = HermesDeliveryAttempt(
            attempt_id=hermes_attempt_id(event.delivery_id, attempt_number),
            delivery_id=event.delivery_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            claimed_at=now,
            lease_expires_at=now + dt.timedelta(seconds=lease_seconds),
        )
        self._connection.execute(
            "INSERT INTO hermes_delivery_attempts VALUES (?, ?, ?, ?, ?)",
            (
                attempt.attempt_id,
                event.delivery_id,
                attempt_number,
                _iso(attempt.lease_expires_at),
                attempt.model_dump_json(),
            ),
        )
        root_message = self._root_message(event.root_delivery_id)
        self._connection.commit()
        lineage = HermesReplyLineage(
            delivery_id=event.delivery_id,
            root_delivery_id=event.root_delivery_id,
            root_platform_message_id=root_message,
        )
        return HermesDeliveryClaim(event=event, attempt=attempt, lineage=lineage)

    def acknowledge(
        self, claim: HermesDeliveryClaim, *, platform_message_id: str, acknowledged_at: dt.datetime
    ) -> bool:
        self._require_active()
        acknowledgement = HermesDeliveryAcknowledgement(
            acknowledgement_id=hermes_acknowledgement_id(claim.event.delivery_id, platform_message_id),
            delivery_id=claim.event.delivery_id,
            attempt_id=claim.attempt.attempt_id,
            platform_message_id=platform_message_id,
            acknowledged_at=acknowledged_at,
        )
        existing: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM hermes_delivery_acknowledgements WHERE delivery_id = ?",
            (claim.event.delivery_id,),
        ).fetchone()
        if existing is not None:
            if existing != (acknowledgement.model_dump_json(),):
                raise HermesDeliveryConflictError
            return False
        self._require_claim(claim, acknowledged_at)
        self._connection.execute(
            "INSERT INTO hermes_delivery_acknowledgements VALUES (?, ?, ?, ?, ?)",
            (
                acknowledgement.acknowledgement_id,
                acknowledgement.delivery_id,
                acknowledgement.attempt_id,
                acknowledgement.platform_message_id,
                acknowledgement.model_dump_json(),
            ),
        )
        self._connection.commit()
        return True

    def fail(
        self,
        claim: HermesDeliveryClaim,
        *,
        failed_at: dt.datetime,
        reason: str,
        retry_delay_seconds: int,
    ) -> HermesDeliveryTransition:
        self._require_active()
        if retry_delay_seconds < 0 or retry_delay_seconds > 3600:
            raise InvalidHermesDeliveryOperationError("invalid Hermes delivery retry delay")
        self._require_claim(claim, failed_at)
        kind = (
            HermesDeliveryTransitionKind.DEAD_LETTER
            if claim.attempt.attempt_number >= claim.event.max_attempts
            else HermesDeliveryTransitionKind.RETRY_SCHEDULED
        )
        available_at = (
            None
            if kind is HermesDeliveryTransitionKind.DEAD_LETTER
            else failed_at + dt.timedelta(seconds=retry_delay_seconds)
        )
        transition = HermesDeliveryTransition(
            transition_id=hermes_transition_id(claim.attempt.attempt_id, kind),
            delivery_id=claim.event.delivery_id,
            attempt_id=claim.attempt.attempt_id,
            kind=kind,
            occurred_at=failed_at,
            available_at=available_at,
            reason=reason,
        )
        self._connection.execute(
            "INSERT INTO hermes_delivery_transitions VALUES (?, ?, ?, ?, ?, ?)",
            (
                transition.transition_id,
                transition.delivery_id,
                transition.attempt_id,
                transition.kind.value,
                None if available_at is None else _iso(available_at),
                transition.model_dump_json(),
            ),
        )
        self._connection.commit()
        return transition

    def close(self) -> None:
        self._active = False

    def _require_claim(self, claim: HermesDeliveryClaim, at: dt.datetime) -> None:
        if (
            at.tzinfo is None
            or at.utcoffset() is None
            or at.astimezone(dt.UTC) > claim.attempt.lease_expires_at.astimezone(dt.UTC)
        ):
            raise HermesDeliveryLeaseLostError
        latest: tuple[str] | None = self._connection.execute(
            """SELECT attempt_id FROM hermes_delivery_attempts
            WHERE delivery_id = ? ORDER BY attempt_number DESC LIMIT 1""",
            (claim.event.delivery_id,),
        ).fetchone()
        terminal = self._connection.execute(
            """SELECT 1 FROM hermes_delivery_transitions WHERE attempt_id = ?
            UNION ALL
            SELECT 1 FROM hermes_delivery_acknowledgements WHERE delivery_id = ?""",
            (claim.attempt.attempt_id, claim.event.delivery_id),
        ).fetchone()
        if latest != (claim.attempt.attempt_id,) or terminal is not None:
            raise HermesDeliveryLeaseLostError

    def _event_exists(self, delivery_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM hermes_delivery_events WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            is not None
        )

    def _root_message(self, delivery_id: str) -> str | None:
        row: tuple[str] | None = self._connection.execute(
            "SELECT platform_message_id FROM hermes_delivery_acknowledgements WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        return None if row is None else row[0]

    def _require_active(self) -> None:
        if not self._active:
            raise InvalidHermesDeliveryStoreError


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()
