from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal, Protocol, Self, assert_never

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_reasoning_codec import (
    AutonomousStructuredReasoner,
    require_canonical_json,
    require_sorted_unique,
)
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousTaskStep,
)

_HASH: Final = r"^[a-f0-9]{64}$"
_TOOL_NAME: Final = r"^[a-z][a-z0-9_.-]{2,63}$"
_TOOL_SIGNATURE: Final = re.compile(
    r"^[a-z][a-z0-9_.-]{2,63}\((?:[a-z][a-z0-9_.-]{2,63}(?:,[a-z][a-z0-9_.-]{2,63})*)?\)$"
)
_ARGUMENT_KEY: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MAX_JSON_BYTES: Final = 16_384


class InvalidAutonomousReasoningError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class AutonomousToolArguments(RootModel[Mapping[str, str]]):
    model_config = ConfigDict(frozen=True, strict=True)

    root: Mapping[str, str]

    @model_validator(mode="after")
    def require_bounded_keys_and_values(self) -> Self:
        values = dict(self.root)
        if len(values) > 8 or any(
            _ARGUMENT_KEY.fullmatch(key) is None or not value or len(value) > 500 for key, value in values.items()
        ):
            raise InvalidAutonomousReasoningError(reason="autonomous_tool_arguments_invalid")
        object.__setattr__(self, "root", MappingProxyType(dict(sorted(values.items()))))
        return self

    @model_serializer
    def serialize_canonical(self) -> dict[str, str]:
        return dict(sorted(self.root.items()))


class AutonomousToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["tool_call"] = "tool_call"
    tool_name: str = Field(pattern=_TOOL_NAME)
    args: AutonomousToolArguments
    reason: str = Field(min_length=8, max_length=500)


class AutonomousDelegate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["delegate"] = "delegate"
    role: AutonomousAgentRole
    objective: str = Field(min_length=8, max_length=2_000)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def reject_supervisor(self) -> Self:
        if self.role is AutonomousAgentRole.SUPERVISOR:
            raise InvalidAutonomousReasoningError(reason="autonomous_delegate_role_denied")
        return self


class AutonomousSubmitArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["submit_artifact"] = "submit_artifact"
    artifact_kind: Literal["context", "hypothesis", "recommendation", "no_trade", "review"]
    artifact_json: str = Field(min_length=2, max_length=_MAX_JSON_BYTES)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, min_length=1, max_length=160)
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("next_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_canonical_artifact_and_wake(self) -> Self:
        require_canonical_json(self.artifact_json, reason="autonomous_artifact_json_invalid")
        require_sorted_unique(self.evidence_refs, reason="autonomous_artifact_refs_invalid")
        has_wake = (self.next_wake_at is None) == (self.next_wake_event is None)
        match self.artifact_kind:
            case "no_trade":
                if has_wake:
                    raise InvalidAutonomousReasoningError(reason="autonomous_no_trade_wake_required")
            case "context" | "hypothesis" | "recommendation" | "review":
                if not has_wake:
                    raise InvalidAutonomousReasoningError(reason="autonomous_artifact_wake_forbidden")
            case unreachable:
                assert_never(unreachable)
        return self


class AutonomousRecordMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["record_memory"] = "record_memory"
    scope: AutonomousMemoryScope
    memory_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    summary: str = Field(min_length=8, max_length=4_000)
    fact_refs: tuple[str, ...] = Field(default=(), max_length=64)
    inference_refs: tuple[str, ...] = Field(default=(), max_length=64)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_memory_lineage(self) -> Self:
        for refs, reason in (
            (self.fact_refs, "autonomous_memory_fact_refs_invalid"),
            (self.inference_refs, "autonomous_memory_inference_refs_invalid"),
            (self.subject_refs, "autonomous_memory_subject_refs_invalid"),
            (self.evidence_refs, "autonomous_memory_evidence_refs_invalid"),
        ):
            require_sorted_unique(refs, reason=reason)
        if not self.fact_refs and not self.inference_refs:
            raise InvalidAutonomousReasoningError(reason="autonomous_memory_lineage_required")
        return self


class AutonomousDefer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["defer"] = "defer"
    reason: str = Field(min_length=8, max_length=500)
    resume_condition: str = Field(min_length=8, max_length=2_000)
    next_wake_at: AwareDatetime | None = None
    next_wake_event: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("next_wake_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return None if value is None else value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_one_wake(self) -> Self:
        if (self.next_wake_at is None) == (self.next_wake_event is None):
            raise InvalidAutonomousReasoningError(reason="autonomous_defer_wake_required")
        return self


class AutonomousComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    kind: Literal["complete"] = "complete"
    summary: str = Field(min_length=8, max_length=4_000)
    completion_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_completion_evidence(self) -> Self:
        require_sorted_unique(self.completion_evidence_refs, reason="autonomous_completion_refs_invalid")
        return self


AutonomousReasoningResponse = Annotated[
    AutonomousToolCall
    | AutonomousDelegate
    | AutonomousSubmitArtifact
    | AutonomousRecordMemory
    | AutonomousDefer
    | AutonomousComplete,
    Field(discriminator="kind"),
]
AUTONOMOUS_REASONING_RESPONSE_ADAPTER: Final = TypeAdapter(AutonomousReasoningResponse)


class AutonomousToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    tool_name: str = Field(pattern=_TOOL_NAME)
    call_json: str = Field(min_length=2, max_length=_MAX_JSON_BYTES)
    bounded_json: str = Field(min_length=2, max_length=_MAX_JSON_BYTES)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    call_sha256: str = Field(pattern=_HASH)
    content_sha256: str = Field(pattern=_HASH)

    @field_validator("observed_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_canonical_hashes(self) -> Self:
        require_canonical_json(self.call_json, reason="autonomous_observation_call_invalid")
        require_canonical_json(self.bounded_json, reason="autonomous_observation_json_invalid")
        require_sorted_unique(self.evidence_refs, reason="autonomous_observation_refs_invalid")
        call = AutonomousToolCall.model_validate_json(self.call_json)
        if (
            call.tool_name != self.tool_name
            or hashlib.sha256(self.call_json.encode()).hexdigest() != self.call_sha256
            or hashlib.sha256(self.bounded_json.encode()).hexdigest() != self.content_sha256
            or self.content_sha256 not in self.evidence_refs
        ):
            raise InvalidAutonomousReasoningError(reason="autonomous_observation_hash_invalid")
        return self


class AutonomousReasoningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    now: AwareDatetime
    task: AutonomousResearchTask
    prior_steps: tuple[AutonomousTaskStep, ...] = Field(max_length=32)
    observations: tuple[AutonomousToolObservation, ...] = Field(max_length=16)
    memories: tuple[AutonomousMemoryRecord, ...] = Field(max_length=16)
    allowed_tool_names: tuple[str, ...] = Field(max_length=16)
    allowed_tool_signatures: tuple[str, ...] = Field(max_length=16)
    remaining_budget: AutonomousRunBudget
    current_role: AutonomousAgentRole

    @field_validator("now", mode="after")
    @classmethod
    def normalize_now(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_attributable_state(self) -> Self:
        require_sorted_unique(self.allowed_tool_names, reason="autonomous_allowed_tools_invalid")
        require_sorted_unique(self.allowed_tool_signatures, reason="autonomous_allowed_tools_invalid")
        signature_names = tuple(signature.partition("(")[0] for signature in self.allowed_tool_signatures)
        if (
            any(re.fullmatch(_TOOL_NAME, name) is None for name in self.allowed_tool_names)
            or any(_TOOL_SIGNATURE.fullmatch(signature) is None for signature in self.allowed_tool_signatures)
            or signature_names != self.allowed_tool_names
        ):
            raise InvalidAutonomousReasoningError(reason="autonomous_observation_authority_invalid")
        return self


class AutonomousReasoningClient(Protocol):
    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse: ...


def canonical_reasoning_prompt(request: AutonomousReasoningRequest) -> str:
    from trading_agent.autonomous_reasoning_codec import canonical_reasoning_prompt as build_prompt

    return build_prompt(request, None)


def validate_reasoning_response(
    request: AutonomousReasoningRequest,
    response: AutonomousReasoningResponse,
) -> None:
    from trading_agent.autonomous_reasoning_codec import validate_reasoning_response as validate

    validate(request, response)


__all__ = (
    "AUTONOMOUS_REASONING_RESPONSE_ADAPTER",
    "AutonomousComplete",
    "AutonomousDefer",
    "AutonomousDelegate",
    "AutonomousReasoningClient",
    "AutonomousReasoningRequest",
    "AutonomousReasoningResponse",
    "AutonomousRecordMemory",
    "AutonomousStructuredReasoner",
    "AutonomousSubmitArtifact",
    "AutonomousToolArguments",
    "AutonomousToolCall",
    "AutonomousToolObservation",
    "InvalidAutonomousReasoningError",
    "canonical_reasoning_prompt",
    "validate_reasoning_response",
)
