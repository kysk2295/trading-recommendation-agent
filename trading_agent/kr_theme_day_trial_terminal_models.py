from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class KrThemeDayTrialTerminalReason(StrEnum):
    NO_SHADOW_ENTRY_ARTIFACT = "no_shadow_entry_artifact"
    INCOMPLETE_SHADOW_EXIT_PATH = "incomplete_shadow_exit_path"
    SHADOW_ARTIFACT_LINEAGE_MISMATCH = "shadow_artifact_lineage_mismatch"
    SHADOW_ENTRY_STORE_INVALID = "shadow_entry_store_invalid"
    SHADOW_EXIT_STORE_INVALID = "shadow_exit_store_invalid"


class InvalidKrThemeDayTrialTerminalModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day trial terminal model is invalid"


class KrThemeDayTrialTerminalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    occurred_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if not self.trial_id or self.trial_id != self.trial_id.strip() or not _aware(self.occurred_at):
            raise InvalidKrThemeDayTrialTerminalModelError
        return self


class KrThemeDayTrialTerminalPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    session_date: dt.date
    started_event_key: str
    terminal_kind: TrialEventKind
    reason_codes: tuple[str, ...]
    entry_ids: tuple[str, ...]
    entry_payload_sha256s: tuple[str, ...]
    exit_ids: tuple[str, ...]
    exit_payload_sha256s: tuple[str, ...]
    terminal_at: dt.datetime

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not self.trial_id
            or not self.strategy_version
            or _HEX64.fullmatch(self.started_event_key) is None
            or not _canonical_texts(self.reason_codes)
            or not _ordered_hashes(self.entry_ids)
            or not _aligned_hashes(self.entry_ids, self.entry_payload_sha256s)
            or not _ordered_hashes(self.exit_ids)
            or not _aligned_hashes(self.exit_ids, self.exit_payload_sha256s)
            or not _aware(self.terminal_at)
        ):
            raise InvalidKrThemeDayTrialTerminalModelError
        match self.terminal_kind:
            case TrialEventKind.COMPLETED:
                if self.reason_codes or not self.entry_ids or len(self.entry_ids) != len(self.exit_ids):
                    raise InvalidKrThemeDayTrialTerminalModelError
            case TrialEventKind.CENSORED:
                if not self.reason_codes:
                    raise InvalidKrThemeDayTrialTerminalModelError
            case TrialEventKind.FAILED:
                if not self.reason_codes:
                    raise InvalidKrThemeDayTrialTerminalModelError
            case TrialEventKind.STARTED:
                raise InvalidKrThemeDayTrialTerminalModelError
            case unreachable:
                assert_never(unreachable)
        return self


class KrThemeDayTrialTerminalArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: KrThemeDayTrialTerminalPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.artifact_id != expected:
            raise InvalidKrThemeDayTrialTerminalModelError
        return self


def kr_theme_day_trial_terminal_artifact(
    payload: KrThemeDayTrialTerminalPayload,
) -> KrThemeDayTrialTerminalArtifact:
    validated = KrThemeDayTrialTerminalPayload.model_validate(payload.model_dump(mode="python"))
    artifact_id = hashlib.sha256(canonical_experiment_ledger_json(validated).encode()).hexdigest()
    return KrThemeDayTrialTerminalArtifact(artifact_id=artifact_id, payload=validated)


def _canonical_texts(values: tuple[str, ...]) -> bool:
    return values == tuple(sorted(set(values))) and all(value and value == value.strip() for value in values)


def _ordered_hashes(values: tuple[str, ...]) -> bool:
    return values == tuple(sorted(set(values))) and all(_HEX64.fullmatch(value) for value in values)


def _aligned_hashes(identities: tuple[str, ...], hashes: tuple[str, ...]) -> bool:
    return len(identities) == len(hashes) and all(_HEX64.fullmatch(value) for value in hashes)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
