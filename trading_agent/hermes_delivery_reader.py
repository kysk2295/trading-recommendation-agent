from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_models import (
    HermesDeliveryAcknowledgement,
    HermesDeliveryAttempt,
    HermesDeliveryEvent,
    HermesDeliveryTransition,
    HermesDeliveryTransitionKind,
)
from trading_agent.hermes_delivery_schema import require_hermes_delivery_schema


class HermesDeliveryReader:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def events(self) -> tuple[HermesDeliveryEvent, ...]:
        return self._models("hermes_delivery_events", HermesDeliveryEvent)

    def attempts(self) -> tuple[HermesDeliveryAttempt, ...]:
        return self._models("hermes_delivery_attempts", HermesDeliveryAttempt)

    def acknowledgements(self) -> tuple[HermesDeliveryAcknowledgement, ...]:
        return self._models("hermes_delivery_acknowledgements", HermesDeliveryAcknowledgement)

    def dead_letters(self) -> tuple[HermesDeliveryTransition, ...]:
        return tuple(
            item
            for item in self._models("hermes_delivery_transitions", HermesDeliveryTransition)
            if item.kind is HermesDeliveryTransitionKind.DEAD_LETTER
        )

    def _models[ModelT: BaseModel](self, table: str, model: type[ModelT]) -> tuple[ModelT, ...]:
        if not self.path.is_file():
            return ()
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            _ = connection.execute("PRAGMA query_only = ON")
            require_hermes_delivery_schema(connection)
            rows: list[tuple[str]] = connection.execute(f"SELECT payload_json FROM {table} ORDER BY rowid").fetchall()
        try:
            return tuple(model.model_validate_json(row[0]) for row in rows)
        except (ValidationError, ValueError) as error:
            raise InvalidHermesDeliveryStoreError from error
