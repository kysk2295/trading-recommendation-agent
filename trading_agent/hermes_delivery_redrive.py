from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import (
    InvalidHermesDeliveryModelError,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_schema import UnsupportedHermesDeliverySchemaError
from trading_agent.hermes_delivery_store import HermesDeliveryStore

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")


class InvalidHermesDeliveryRedriveError(ValueError):
    @override
    def __str__(self) -> str:
        return "Hermes delivery redrive is invalid"


@dataclass(frozen=True, slots=True)
class HermesDeliveryRedriveRequest:
    dead_letter_transition_id: str


@dataclass(frozen=True, slots=True)
class HermesDeliveryRedriveResult:
    inserted: int
    replayed: int


def redrive_timeout_dead_letter(
    store: HermesDeliveryStore,
    request: HermesDeliveryRedriveRequest,
) -> HermesDeliveryRedriveResult:
    if _HEX64.fullmatch(request.dead_letter_transition_id) is None or not store.path.is_file():
        raise InvalidHermesDeliveryRedriveError
    try:
        with store.writer() as writer:
            transitions = tuple(
                item for item in store.dead_letters() if item.transition_id == request.dead_letter_transition_id
            )
            if len(transitions) != 1:
                raise InvalidHermesDeliveryRedriveError
            transition = transitions[0]
            originals = tuple(item for item in store.events() if item.delivery_id == transition.delivery_id)
            attempts = tuple(
                sorted(
                    (item for item in store.attempts() if item.delivery_id == transition.delivery_id),
                    key=lambda item: item.attempt_number,
                )
            )
            acknowledged = any(item.delivery_id == transition.delivery_id for item in store.acknowledgements())
            if (
                len(originals) != 1
                or transition.reason != "telegram_timeout"
                or acknowledged
                or not attempts
                or attempts[-1].attempt_id != transition.attempt_id
                or len(attempts) != originals[0].max_attempts
                or originals[0].root_delivery_id != originals[0].delivery_id
            ):
                raise InvalidHermesDeliveryRedriveError
            original = originals[0]
            identity_material = (original.delivery_id, transition.transition_id)
            identity = hashlib.sha256(
                json.dumps(identity_material, ensure_ascii=True, separators=(",", ":")).encode()
            ).hexdigest()
            payload = hashlib.sha256(
                json.dumps(
                    (original.model_dump(mode="json"), transition.model_dump(mode="json")),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            event = build_hermes_delivery_event(
                kind=original.kind,
                source_event_id=f"hermes-redrive-{identity}",
                market_id=original.market_id,
                lane_id=original.lane_id,
                occurred_at=transition.occurred_at,
                payload_sha256=payload,
                rendered_text=original.rendered_text,
                agent_family=original.agent_family,
                instrument_id=original.instrument_id,
                strategy_version=original.strategy_version,
                status=original.status,
                evidence_refs=tuple(
                    sorted((*original.evidence_refs, f"hermes-dead-letter:{transition.transition_id}"))
                ),
                max_attempts=original.max_attempts,
            )
            inserted = int(writer.append_event(event).inserted)
        return HermesDeliveryRedriveResult(inserted=inserted, replayed=1 - inserted)
    except InvalidHermesDeliveryRedriveError:
        raise
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryModelError,
        InvalidHermesDeliveryStoreError,
        UnsupportedHermesDeliverySchemaError,
        OSError,
        sqlite3.Error,
        TypeError,
    ):
        raise InvalidHermesDeliveryRedriveError from None


__all__ = (
    "HermesDeliveryRedriveRequest",
    "HermesDeliveryRedriveResult",
    "InvalidHermesDeliveryRedriveError",
    "redrive_timeout_dead_letter",
)
