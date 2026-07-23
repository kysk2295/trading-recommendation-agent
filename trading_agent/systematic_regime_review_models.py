from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_models import TrialEventKind

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CURRENT_SYSTEMATIC_REGIME_REVIEWER_VERSION: Final = "systematic_regime_reviewer_v1"


class SystematicRegimeReviewerAction(StrEnum):
    CONTINUE_COLLECTION = "continue_collection"
    DATA_QUALITY_REVIEW = "data_quality_review"


class SystematicRegimeReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    card_id: str
    trial_id: str
    strategy_version: str
    experiment_scope_key: str
    terminal_event_key: str
    terminal_kind: TrialEventKind
    artifact_sha256s: tuple[str, ...]
    outcome_artifact_sha256: str | None
    reviewer_version: str
    reviewer_action: SystematicRegimeReviewerAction
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    reviewed_at: dt.datetime
    automatic_state_change_allowed: Literal[False]
    order_authority_change_allowed: Literal[False]
    allocation_change_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        completed = self.terminal_kind is TrialEventKind.COMPLETED
        if (
            not _canonical_text(self.card_id, max_length=512)
            or not all(
                _IDENTIFIER.fullmatch(value)
                for value in (
                    self.trial_id,
                    self.strategy_version,
                    self.reviewer_version,
                )
            )
            or not _HEX64.fullmatch(self.experiment_scope_key)
            or not _HEX64.fullmatch(self.terminal_event_key)
            or self.terminal_kind not in (TrialEventKind.COMPLETED, TrialEventKind.CENSORED)
            or completed != bool(self.artifact_sha256s)
            or completed != (self.outcome_artifact_sha256 is not None)
            or not _canonical_hashes(self.artifact_sha256s, required=completed)
            or (
                self.outcome_artifact_sha256 is not None
                and not _HEX64.fullmatch(self.outcome_artifact_sha256)
            )
            or not _canonical_texts(self.reasons)
            or not _canonical_texts(self.blockers)
            or not _aware(self.reviewed_at)
        ):
            raise ValueError("invalid immutable systematic regime review event")
        return self


def _canonical_hashes(values: tuple[str, ...], *, required: bool) -> bool:
    return (
        (bool(values) or not required)
        and values == tuple(sorted(set(values)))
        and all(_HEX64.fullmatch(value) for value in values)
    )


def _canonical_texts(values: tuple[str, ...]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(
        _canonical_text(value, max_length=512) for value in values
    )


def _canonical_text(value: str, *, max_length: int) -> bool:
    return bool(value) and len(value) <= max_length and value == value.strip()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "CURRENT_SYSTEMATIC_REGIME_REVIEWER_VERSION",
    "SystematicRegimeReviewEvent",
    "SystematicRegimeReviewerAction",
)
