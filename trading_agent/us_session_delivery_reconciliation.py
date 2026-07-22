from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.hermes_delivery_projection import (
    HermesProjectionSources,
    delivery_event_from_projection_record,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_session_delivery_projection import (
    us_session_projection_records,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidUsSessionDeliveryReconciliationError(ValueError):
    @override
    def __str__(self) -> str:
        return "US session delivery reconciliation is invalid"


class UsSessionDeliveryReconciliationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sources: HermesProjectionSources
    session_date: dt.date
    generated_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise InvalidUsSessionDeliveryReconciliationError
        return self


class UsSessionDeliveryReconciliation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    policy_version: Literal["us-session-hermes-reconciliation-v1"] = (
        "us-session-hermes-reconciliation-v1"
    )
    session_date: dt.date
    generated_at: dt.datetime
    source_projection_sha256: str
    expected_delivery_ids: tuple[str, ...]
    projected_delivery_ids: tuple[str, ...]
    acknowledged_delivery_ids: tuple[str, ...]
    dead_letter_delivery_ids: tuple[str, ...]
    pending_delivery_ids: tuple[str, ...]
    expected_count: int
    projected_count: int
    acknowledged_count: int
    dead_letter_count: int
    pending_count: int
    complete: bool

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        expected = set(self.expected_delivery_ids)
        projected = set(self.projected_delivery_ids)
        acknowledged = set(self.acknowledged_delivery_ids)
        dead = set(self.dead_letter_delivery_ids)
        pending = set(self.pending_delivery_ids)
        sequences = (
            self.expected_delivery_ids,
            self.projected_delivery_ids,
            self.acknowledged_delivery_ids,
            self.dead_letter_delivery_ids,
            self.pending_delivery_ids,
        )
        counts = (
            self.expected_count,
            self.projected_count,
            self.acknowledged_count,
            self.dead_letter_count,
            self.pending_count,
        )
        if (
            self.generated_at.tzinfo is None
            or self.generated_at.utcoffset() is None
            or _HEX64.fullmatch(self.source_projection_sha256) is None
            or not expected
            or any(sequence != tuple(sorted(set(sequence))) for sequence in sequences)
            or not projected <= expected
            or not acknowledged <= projected
            or not dead <= projected
            or acknowledged & dead
            or pending != expected - acknowledged - dead
            or counts
            != (
                len(expected),
                len(projected),
                len(acknowledged),
                len(dead),
                len(pending),
            )
            or self.complete != (acknowledged == expected and not dead)
        ):
            raise InvalidUsSessionDeliveryReconciliationError
        return self


def reconcile_us_session_deliveries(
    request: UsSessionDeliveryReconciliationRequest,
    store: HermesDeliveryStore,
) -> UsSessionDeliveryReconciliation:
    request = UsSessionDeliveryReconciliationRequest.model_validate(
        request.model_dump(mode="python")
    )
    records = us_session_projection_records(request.sources, request.session_date)
    if not records or any(record.occurred_at > request.generated_at for record in records):
        raise InvalidUsSessionDeliveryReconciliationError
    expected_events = tuple(delivery_event_from_projection_record(record) for record in records)
    expected_by_id = {event.delivery_id: event for event in expected_events}
    if len(expected_by_id) != len(expected_events):
        raise InvalidUsSessionDeliveryReconciliationError
    persisted_by_id = {
        event.delivery_id: event
        for event in store.events()
        if event.delivery_id in expected_by_id
    }
    if any(event != expected_by_id[delivery_id] for delivery_id, event in persisted_by_id.items()):
        raise InvalidUsSessionDeliveryReconciliationError
    acknowledged = {
        item.delivery_id
        for item in store.acknowledgements()
        if item.delivery_id in expected_by_id
    }
    dead = {
        item.delivery_id
        for item in store.dead_letters()
        if item.delivery_id in expected_by_id
    }
    expected = set(expected_by_id)
    projected = set(persisted_by_id)
    pending = expected - acknowledged - dead
    ordered_records = tuple(sorted(records, key=lambda item: item.source_event_id))
    source_digest = hashlib.sha256(
        b"\n".join(record.model_dump_json().encode() for record in ordered_records)
    ).hexdigest()
    return UsSessionDeliveryReconciliation(
        session_date=request.session_date,
        generated_at=request.generated_at,
        source_projection_sha256=source_digest,
        expected_delivery_ids=tuple(sorted(expected)),
        projected_delivery_ids=tuple(sorted(projected)),
        acknowledged_delivery_ids=tuple(sorted(acknowledged)),
        dead_letter_delivery_ids=tuple(sorted(dead)),
        pending_delivery_ids=tuple(sorted(pending)),
        expected_count=len(expected),
        projected_count=len(projected),
        acknowledged_count=len(acknowledged),
        dead_letter_count=len(dead),
        pending_count=len(pending),
        complete=acknowledged == expected and not dead,
    )


def write_us_session_delivery_reconciliation(
    destination: Path,
    report: UsSessionDeliveryReconciliation,
) -> None:
    validated = UsSessionDeliveryReconciliation.model_validate(
        report.model_dump(mode="python")
    )
    write_private_stable_report(
        destination,
        validated.model_dump_json(indent=2) + "\n",
    )
