from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.kr_loop_engineer_receipts import (
    KrLoopHealthReceipt,
    KrLoopShadowReceipt,
    KrLoopValidationReceipt,
)

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"
_GIT_SHA = r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"


class KrLoopCandidateState(StrEnum):
    DETECTED = "detected"
    CANDIDATE_READY = "candidate_ready"
    SHADOWING = "shadowing"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class KrLoopReleaseAction(StrEnum):
    PROMOTE = "promote"
    ROLLBACK = "rollback"


class InvalidKrLoopEngineerModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer model is invalid"


class KrLoopCandidateSnapshot(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=_SHA)
    candidate_id: str = Field(pattern=_SHA)
    previous_snapshot_id: str | None = Field(default=None, pattern=_SHA)
    bundle_id: str = Field(pattern=_SHA)
    base_commit: str = Field(pattern=_GIT_SHA)
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=16)
    state: KrLoopCandidateState
    candidate_commit: str | None = Field(default=None, pattern=_GIT_SHA)
    patch_sha256: str | None = Field(default=None, pattern=_SHA)
    verification_sha256: str | None = Field(default=None, pattern=_SHA)
    shadow_receipts: tuple[KrLoopShadowReceipt, ...] = Field(max_length=32)
    health_receipts: tuple[KrLoopHealthReceipt, ...] = Field(max_length=32)
    reason_codes: tuple[str, ...] = Field(max_length=16)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False
    policy_mutation_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if (
            self.allowed_paths != tuple(sorted(set(self.allowed_paths)))
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or self.created_at > self.updated_at
            or self.candidate_id != _candidate_id(self.bundle_id, self.base_commit, self.allowed_paths)
            or self.snapshot_id != _identity(self, "snapshot_id")
        ):
            raise InvalidKrLoopEngineerModelError
        self._validate_state_fields()
        return self

    def _validate_state_fields(self) -> None:
        artifact = self.candidate_commit is not None and self.patch_sha256 is not None
        verified = artifact and self.verification_sha256 is not None
        match self.state:
            case KrLoopCandidateState.DETECTED:
                valid = (
                    not artifact
                    and not verified
                    and not self.shadow_receipts
                    and not self.health_receipts
                    and not self.reason_codes
                )
            case KrLoopCandidateState.CANDIDATE_READY:
                valid = (
                    artifact
                    and self.verification_sha256 is None
                    and not self.shadow_receipts
                    and not self.health_receipts
                    and not self.reason_codes
                )
            case KrLoopCandidateState.SHADOWING:
                valid = verified and not self.health_receipts and not self.reason_codes
            case KrLoopCandidateState.PROMOTED:
                valid = (
                    verified
                    and len({item.session_date for item in self.shadow_receipts}) >= 2
                    and not self.health_receipts
                    and not self.reason_codes
                )
            case KrLoopCandidateState.REJECTED:
                valid = bool(self.reason_codes) and not self.health_receipts
            case KrLoopCandidateState.ROLLED_BACK:
                valid = (
                    verified
                    and len(self.shadow_receipts) >= 2
                    and bool(self.health_receipts)
                    and bool(self.reason_codes)
                )
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise InvalidKrLoopEngineerModelError


class KrLoopReleaseEvent(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1] = 1
    release_id: str = Field(pattern=_SHA)
    generation: int = Field(ge=1)
    action: KrLoopReleaseAction
    candidate_id: str = Field(pattern=_SHA)
    active_commit: str = Field(pattern=_GIT_SHA)
    previous_commit: str = Field(pattern=_GIT_SHA)
    patch_sha256: str = Field(pattern=_SHA)
    recorded_at: AwareDatetime
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_release(self) -> Self:
        if self.active_commit == self.previous_commit or self.release_id != _identity(self, "release_id"):
            raise InvalidKrLoopEngineerModelError
        return self


def build_candidate_snapshot(
    *,
    bundle_id: str,
    base_commit: str,
    allowed_paths: tuple[str, ...],
    state: KrLoopCandidateState,
    updated_at: dt.datetime,
    previous: KrLoopCandidateSnapshot | None = None,
    candidate_commit: str | None = None,
    patch_sha256: str | None = None,
    verification_sha256: str | None = None,
    shadow_receipts: tuple[KrLoopShadowReceipt, ...] = (),
    health_receipts: tuple[KrLoopHealthReceipt, ...] = (),
    reason_codes: tuple[str, ...] = (),
) -> KrLoopCandidateSnapshot:
    paths = tuple(sorted(set(allowed_paths)))
    draft = KrLoopCandidateSnapshot.model_construct(
        snapshot_id="",
        candidate_id=_candidate_id(bundle_id, base_commit, paths),
        previous_snapshot_id=None if previous is None else previous.snapshot_id,
        bundle_id=bundle_id,
        base_commit=base_commit,
        allowed_paths=paths,
        state=state,
        candidate_commit=candidate_commit,
        patch_sha256=patch_sha256,
        verification_sha256=verification_sha256,
        shadow_receipts=tuple(sorted(shadow_receipts, key=lambda item: (item.session_date, item.observed_at))),
        health_receipts=tuple(sorted(health_receipts, key=lambda item: item.observed_at)),
        reason_codes=tuple(sorted(set(reason_codes))),
        created_at=updated_at if previous is None else previous.created_at,
        updated_at=updated_at,
    )
    return KrLoopCandidateSnapshot.model_validate(
        draft.model_copy(update={"snapshot_id": _identity(draft, "snapshot_id")}).model_dump(mode="python")
    )


def build_release_event(
    *,
    action: KrLoopReleaseAction,
    candidate: KrLoopCandidateSnapshot,
    previous: KrLoopReleaseEvent | None,
    recorded_at: dt.datetime,
) -> KrLoopReleaseEvent:
    if candidate.candidate_commit is None or candidate.patch_sha256 is None:
        raise InvalidKrLoopEngineerModelError
    generation = 1 if previous is None else previous.generation + 1
    match action:
        case KrLoopReleaseAction.PROMOTE:
            active_commit = candidate.candidate_commit
            previous_commit = candidate.base_commit if previous is None else previous.active_commit
        case KrLoopReleaseAction.ROLLBACK:
            active_commit = candidate.base_commit
            previous_commit = candidate.candidate_commit
        case unreachable:
            assert_never(unreachable)
    draft = KrLoopReleaseEvent.model_construct(
        release_id="",
        generation=generation,
        action=action,
        candidate_id=candidate.candidate_id,
        active_commit=active_commit,
        previous_commit=previous_commit,
        patch_sha256=candidate.patch_sha256,
        recorded_at=recorded_at,
    )
    return KrLoopReleaseEvent.model_validate(
        draft.model_copy(update={"release_id": _identity(draft, "release_id")}).model_dump(mode="python")
    )


def _candidate_id(bundle_id: str, base_commit: str, paths: tuple[str, ...]) -> str:
    payload = json.dumps((bundle_id, base_commit, paths), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _identity(model: BaseModel, field: str) -> str:
    payload = json.dumps(
        model.model_dump(mode="json", exclude={field}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


__all__ = (
    "InvalidKrLoopEngineerModelError",
    "KrLoopCandidateSnapshot",
    "KrLoopCandidateState",
    "KrLoopHealthReceipt",
    "KrLoopReleaseAction",
    "KrLoopReleaseEvent",
    "KrLoopShadowReceipt",
    "KrLoopValidationReceipt",
    "build_candidate_snapshot",
    "build_release_event",
)
