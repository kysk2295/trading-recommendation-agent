from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never, override

from pydantic import TypeAdapter, ValidationError

from trading_agent.private_immutable_file import read_private_text
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
    SystematicInputActivation,
)

_ACTIVATION_ADAPTER: Final = TypeAdapter(SystematicInputActivation)


@dataclass(frozen=True, slots=True)
class InvalidSystematicInputActivationError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"systematic input activation invalid: {self.reason}"


def canonical_systematic_input_activation_json(activation: SystematicInputActivation) -> str:
    try:
        checked = _ACTIVATION_ADAPTER.validate_python(activation)
        return _canonical_text(checked)
    except (TypeError, ValidationError, ValueError):
        raise InvalidSystematicInputActivationError("serialization_invalid") from None


def write_systematic_input_activation(
    path: Path,
    activation: SystematicInputActivation,
) -> None:
    try:
        checked = _ACTIVATION_ADAPTER.validate_python(activation)
        _validate_referenced_artifacts(checked)
        payload = _canonical_text(checked)
        write_private_stable_report(path, payload)
    except InvalidSystematicInputActivationError:
        raise
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidSystematicInputActivationError("write_invalid") from None


def load_systematic_input_activation(path: Path) -> SystematicInputActivation:
    try:
        payload = read_private_text(path)
        activation = _ACTIVATION_ADAPTER.validate_json(payload)
        if payload != _canonical_text(activation):
            raise InvalidSystematicInputActivationError("payload_not_canonical")
        _validate_referenced_artifacts(activation)
        return activation
    except InvalidSystematicInputActivationError:
        raise
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidSystematicInputActivationError("read_invalid") from None


def _validate_referenced_artifacts(activation: SystematicInputActivation) -> None:
    match activation:
        case BlockedSystematicInputActivation(
            attempt_report_path=Path() as report_path,
            attempt_report_sha256=str() as expected_sha,
        ):
            payload = read_private_text(report_path)
            if hashlib.sha256(payload.encode()).hexdigest() != expected_sha:
                raise InvalidSystematicInputActivationError("attempt_report_digest_mismatch")
        case BlockedSystematicInputActivation():
            return
        case ReadySystematicInputActivation() as ready:
            references = (
                (ready.input_csv_path, ready.input_csv_sha256),
                (ready.dataset_receipt_path, ready.dataset_receipt_sha256),
                (ready.catalog_receipt_path, ready.catalog_receipt_sha256),
                (ready.input_binding_receipt_path, ready.input_binding_receipt_sha256),
                (ready.foundation_path, ready.foundation_sha256),
            )
            for artifact_path, expected_sha in references:
                payload = read_private_text(artifact_path)
                if hashlib.sha256(payload.encode()).hexdigest() != expected_sha:
                    raise InvalidSystematicInputActivationError("artifact_digest_mismatch")
        case unreachable:
            assert_never(unreachable)


def _canonical_text(activation: SystematicInputActivation) -> str:
    return json.dumps(
        _ACTIVATION_ADAPTER.dump_python(activation, mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


__all__ = (
    "InvalidSystematicInputActivationError",
    "canonical_systematic_input_activation_json",
    "load_systematic_input_activation",
    "write_systematic_input_activation",
)
