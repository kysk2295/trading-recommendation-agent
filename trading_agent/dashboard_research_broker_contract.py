from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

BrokerOperation = Literal["evidence-query", "hypothesis-register", "experiment-run"]


@dataclass(frozen=True, slots=True)
class InvalidResearchBrokerCommandError(RuntimeError):
    reason: str


def encode_broker_command(
    operation: BrokerOperation,
    parameters: tuple[str, ...],
) -> str:
    _validate(operation, parameters)
    return json.dumps(
        {"operation": operation, "parameters": parameters},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_broker_command(raw: str) -> tuple[BrokerOperation, tuple[str, ...]]:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"operation", "parameters"}:
            raise InvalidResearchBrokerCommandError("broker_command_invalid")
        operation = value["operation"]
        parameters = value["parameters"]
        if operation not in {"evidence-query", "hypothesis-register", "experiment-run"}:
            raise InvalidResearchBrokerCommandError("broker_operation_forbidden")
        if not isinstance(parameters, list) or not all(isinstance(item, str) for item in parameters):
            raise InvalidResearchBrokerCommandError("broker_parameters_invalid")
        typed_operation: BrokerOperation = operation
        typed_parameters = tuple(parameters)
        _validate(typed_operation, typed_parameters)
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise InvalidResearchBrokerCommandError("broker_command_invalid") from error
    return typed_operation, typed_parameters


def _validate(operation: BrokerOperation, parameters: tuple[str, ...]) -> None:
    expected = {
        "evidence-query": range(1, 33),
        "hypothesis-register": range(3, 4),
        "experiment-run": range(1, 2),
    }
    if len(parameters) not in expected[operation] or any(not _safe(item) for item in parameters):
        raise InvalidResearchBrokerCommandError("broker_parameters_forbidden")


def _safe(value: str) -> bool:
    return 1 <= len(value) <= 160 and all(character.isalnum() or character in "._:-" for character in value)


__all__ = (
    "BrokerOperation",
    "InvalidResearchBrokerCommandError",
    "decode_broker_command",
    "encode_broker_command",
)
