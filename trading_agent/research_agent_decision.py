from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, Self, final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY, AgentFamilyId
from trading_agent.dashboard_executable_binding import (
    FileIdentity,
    InvalidExecutableBindingError,
    capture_file,
    revalidate,
)
from trading_agent.research_agent_cycle_models import (
    CycleId,
    DecisionId,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkV1,
    ResearchAgentWakeKind,
)

_MAX_RESPONSE_BYTES: Final = 64 * 1024
_MAX_PROMPT_EVIDENCE_BYTES: Final = 48 * 1024
_MODEL_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,127}$")
_PROVIDER_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CLAUDE_MAX_BUDGET_USD: Final = "0.05"
_FAMILY_DEFINITIONS: Final = {definition.family_id: definition for definition in AGENT_FAMILY_REGISTRY}
_FAMILY_DECISIONS: Final[dict[AgentFamilyId, tuple[ResearchAgentDecisionKind, ...]]] = {
    "opportunity_manager": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
    ),
    "day_trading": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
        ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION,
        ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
    ),
    "swing_trading": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
    ),
    "systematic_quant": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT,
        ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
    ),
    "derivatives_research": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.PUBLISH_CONTEXT,
    ),
    "market_context": (
        ResearchAgentDecisionKind.NO_ACTION,
        ResearchAgentDecisionKind.PUBLISH_CONTEXT,
    ),
}


class InvalidResearchAgentDecisionError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class ResearchAgentDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cycle_id: CycleId = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    evidence: tuple[ResearchAgentEvidenceV1, ...] = Field(min_length=1, max_length=32)
    open_work: tuple[ResearchAgentOpenWorkV1, ...] = Field(max_length=16)
    requested_at: AwareDatetime
    max_runtime_seconds: float = Field(gt=0, le=300)
    max_model_calls: Literal[1] = 1
    day_agent_runtime_enabled: bool = False

    @model_validator(mode="after")
    def require_family_isolation(self) -> Self:
        if self.agent_family_id == "day_trading" and self.day_agent_runtime_enabled:
            raise InvalidResearchAgentDecisionError(reason="persistent_day_runtime_required")
        if any(item.agent_family_id != self.agent_family_id for item in (*self.evidence, *self.open_work)):
            raise InvalidResearchAgentDecisionError(reason="decision_family_isolation_required")
        return self


class HermesResearchAgentDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    primary_decision: ResearchAgentDecisionKind
    question: str = Field(min_length=8, max_length=500)
    summary: str = Field(min_length=8, max_length=1_000)
    reason: str | None = Field(min_length=3, max_length=160)
    continuation: str | None = Field(min_length=8, max_length=500)
    open_work_ref: str | None = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    requested_action: ResearchAgentDecisionKind | None
    subject_refs: tuple[str, ...] = Field(max_length=32)
    next_wake_kind: ResearchAgentWakeKind
    next_wake_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_single_action(self) -> Self:
        no_action = self.primary_decision is ResearchAgentDecisionKind.NO_ACTION
        if self.subject_refs != tuple(sorted(set(self.subject_refs))) or any(
            not _safe_reference(reference) for reference in self.subject_refs
        ):
            raise InvalidResearchAgentDecisionError(reason="decision_subject_invalid")
        if no_action != (not self.subject_refs):
            raise InvalidResearchAgentDecisionError(reason="decision_subject_required")
        if no_action != (self.requested_action is None):
            raise InvalidResearchAgentDecisionError(reason="single_primary_action_required")
        if not no_action and self.requested_action is not self.primary_decision:
            raise InvalidResearchAgentDecisionError(reason="single_primary_action_required")
        if no_action and (self.reason is None or self.continuation is None):
            raise InvalidResearchAgentDecisionError(reason="no_action_continuation_required")
        if (self.next_wake_kind is ResearchAgentWakeKind.SCHEDULED) != (self.next_wake_at is not None):
            raise InvalidResearchAgentDecisionError(reason="scheduled_wake_time_required")
        return self


@dataclass(frozen=True, slots=True)
class ResearchAgentDecisionParseContext:
    request: ResearchAgentDecisionRequest
    model_id: str
    prompt_sha256: str


class ResearchAgentDecisionClient(Protocol):
    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1: ...


@final
class ClaudeCliResearchAgentDecisionClient:
    __slots__ = ("_executable", "_model_id", "_timeout_seconds")

    _executable: FileIdentity
    _model_id: str
    _timeout_seconds: float

    def __init__(self, executable: Path, model_id: str, *, timeout_seconds: float = 120.0) -> None:
        try:
            self._executable = capture_file(executable, executable=True)
        except InvalidExecutableBindingError:
            raise InvalidResearchAgentDecisionError(reason="claude_executable_invalid") from None
        if _MODEL_ID.fullmatch(model_id) is None or timeout_seconds <= 0 or timeout_seconds > 300:
            raise InvalidResearchAgentDecisionError(reason="claude_client_config_invalid")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds

    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        prompt = render_research_agent_prompt(request)
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        schema = json.dumps(
            HermesResearchAgentDecisionResponse.model_json_schema(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            revalidate(self._executable, executable=True)
            completed = subprocess.run(
                (
                    str(self._executable.path),
                    "-p",
                    "--safe-mode",
                    "--disable-slash-commands",
                    "--tools",
                    "",
                    "--no-session-persistence",
                    "--model",
                    self._model_id,
                    "--max-budget-usd",
                    _CLAUDE_MAX_BUDGET_USD,
                    "--json-schema",
                    schema,
                    "--output-format",
                    "json",
                    prompt,
                ),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=min(self._timeout_seconds, request.max_runtime_seconds),
                env=_claude_environment(self._executable.path),
            )
        except (InvalidExecutableBindingError, OSError, subprocess.SubprocessError, ValueError):
            raise InvalidResearchAgentDecisionError(reason="claude_decision_call_failed") from None
        if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_RESPONSE_BYTES:
            raise InvalidResearchAgentDecisionError(reason="claude_decision_call_failed")
        try:
            wrapper = json.loads(completed.stdout)
            if not isinstance(wrapper, dict) or wrapper.get("is_error") is not False:
                raise ValueError
            structured = wrapper["structured_output"]
            if not isinstance(structured, dict):
                raise ValueError
            payload = json.dumps(
                structured,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        except (KeyError, TypeError, ValueError):
            raise InvalidResearchAgentDecisionError(reason="claude_decision_response_invalid") from None
        return parse_research_agent_decision(
            payload,
            ResearchAgentDecisionParseContext(request, self._model_id, prompt_sha256),
        )


def _claude_environment(executable: Path) -> dict[str, str]:
    account = pwd.getpwuid(os.geteuid())
    return {
        "HOME": account.pw_dir,
        "LOGNAME": account.pw_name,
        "PATH": f"{executable.parent}{os.pathsep}{os.defpath}",
        "SHELL": account.pw_shell,
        "TMPDIR": tempfile.gettempdir(),
        "USER": account.pw_name,
    }


@final
class HermesCliResearchAgentDecisionClient:
    __slots__ = ("_executable", "_model_id", "_provider_id", "_timeout_seconds")

    _executable: FileIdentity
    _model_id: str
    _provider_id: str
    _timeout_seconds: float

    def __init__(
        self,
        executable: Path,
        model_id: str,
        provider_id: str,
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        try:
            self._executable = capture_file(executable, executable=True)
        except InvalidExecutableBindingError:
            raise InvalidResearchAgentDecisionError(reason="hermes_executable_invalid") from None
        if (
            _MODEL_ID.fullmatch(model_id) is None
            or _PROVIDER_ID.fullmatch(provider_id) is None
            or timeout_seconds <= 0
            or timeout_seconds > 300
        ):
            raise InvalidResearchAgentDecisionError(reason="hermes_client_config_invalid")
        self._model_id = model_id
        self._provider_id = provider_id
        self._timeout_seconds = timeout_seconds

    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        prompt = render_research_agent_prompt(request)
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        try:
            revalidate(self._executable, executable=True)
            completed = subprocess.run(
                (
                    str(self._executable.path),
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--provider",
                    self._provider_id,
                    "-m",
                    self._model_id,
                    "-t",
                    "",
                    "-z",
                    prompt,
                ),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=min(self._timeout_seconds, request.max_runtime_seconds),
                env={"PATH": "/usr/bin:/bin"},
            )
        except (InvalidExecutableBindingError, OSError, subprocess.SubprocessError, ValueError):
            raise InvalidResearchAgentDecisionError(reason="hermes_decision_call_failed") from None
        if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_RESPONSE_BYTES:
            raise InvalidResearchAgentDecisionError(reason="hermes_decision_call_failed")
        return parse_research_agent_decision(
            completed.stdout,
            ResearchAgentDecisionParseContext(request, self._model_id, prompt_sha256),
        )


def render_research_agent_prompt(request: ResearchAgentDecisionRequest) -> str:
    definition = _FAMILY_DEFINITIONS[request.agent_family_id]
    allowed_decisions = ",".join(kind.value for kind in _FAMILY_DECISIONS[request.agent_family_id])
    family_instruction = {
        "opportunity_manager": (
            " A non-blocked opportunity already passed source, completed-bar, spread, and session admission; "
            "use propose_hypothesis to create a falsifiable research protocol, not a trade recommendation."
        ),
        "day_trading": (
            " Use bounded point-in-time Day evidence to request one falsifiable hypothesis proposal; "
            "never specify provider, broker, order, sizing, risk, or trading authority."
        ),
    }.get(request.agent_family_id, "")
    evidence: list[dict[str, object]] = []
    payload_bytes = 0
    for item in request.evidence:
        if item.bounded_payload_json is None:
            raise InvalidResearchAgentDecisionError(reason="bounded_payload_missing")
        payload_bytes += len(item.bounded_payload_json.encode())
        if payload_bytes > _MAX_PROMPT_EVIDENCE_BYTES:
            raise InvalidResearchAgentDecisionError(reason="bounded_payload_limit_exceeded")
        evidence.append(
            {
                "evidence_id": item.evidence_id,
                "evidence_refs": item.evidence_refs,
                "market_id": item.market_id,
                "observed_at": item.observed_at.isoformat(),
                "payload": json.loads(item.bounded_payload_json),
                "payload_truncated": item.payload_truncated,
                "source_key": item.source_key,
                "subject_refs": item.subject_refs,
                "trigger_kind": item.trigger_kind,
            }
        )
    open_work = tuple(item.model_dump(mode="json") for item in request.open_work)
    payload = json.dumps(
        {"cycle_id": request.cycle_id, "evidence": evidence, "open_work": open_work},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    schema = json.dumps(
        HermesResearchAgentDecisionResponse.model_json_schema(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        f"<agent-family>{definition.family_id}</agent-family>\n"
        f"<memory-namespace>{definition.memory_namespace}</memory-namespace>\n"
        f"<role>{definition.role}</role>\n"
        f"Allowed primary_decisions for this family: {allowed_decisions}.{family_instruction}\n"
        "Make exactly one evidence-bound research decision. Return one raw JSON object and no prose. "
        "primary_decision=no_action requires requested_action=null and non-null reason and continuation; "
        "primary_decision=no_action requires subject_refs=[]; every other primary_decision requires "
        "requested_action to equal primary_decision and at least one available subject_ref. "
        "next_wake_kind=scheduled requires next_wake_at to be a non-null RFC 3339 timestamp; "
        "every other next_wake_kind requires next_wake_at=null. "
        "Never emit argv, executable or filesystem paths, credentials, account identifiers, price calculations, "
        "Reviewer verdicts, broker instructions, or lifecycle changes. "
        "order authority: false; lifecycle authority: false; allocation authority: false.\n"
        f"<response-schema>{schema}</response-schema>\n"
        f"<cycle-evidence>{payload}</cycle-evidence>"
    )


def parse_research_agent_decision(
    payload: bytes,
    context: ResearchAgentDecisionParseContext,
) -> ResearchAgentDecisionV1:
    response_sha256 = hashlib.sha256(payload).hexdigest()
    try:
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and isinstance(subject_refs := decoded.get("subject_refs"), list) and all(
            isinstance(reference, str) for reference in subject_refs
        ):
            decoded["subject_refs"] = sorted(set(subject_refs))
        response = HermesResearchAgentDecisionResponse.model_validate_json(
            json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )
    except (ValidationError, ValueError):
        raise InvalidResearchAgentDecisionError(reason="hermes_decision_response_invalid") from None
    if response.primary_decision not in _FAMILY_DECISIONS[context.request.agent_family_id]:
        raise InvalidResearchAgentDecisionError(reason="decision_kind_not_allowed")
    available_subjects = {
        reference
        for item in context.request.evidence
        for reference in (str(item.evidence_id), *item.subject_refs)
    } | {item.work_id for item in context.request.open_work}
    if not set(response.subject_refs).issubset(available_subjects):
        raise InvalidResearchAgentDecisionError(reason="decision_subject_unresolved")
    try:
        evidence_refs = tuple(
            sorted({reference for item in context.request.evidence for reference in item.evidence_refs})
        )
        decision_id = DecisionId(
            hashlib.sha256(
                f"{context.request.cycle_id}:{context.model_id}:{response_sha256}:decision-v1".encode()
            ).hexdigest()
        )
        return ResearchAgentDecisionV1(
            decision_id=decision_id,
            cycle_id=context.request.cycle_id,
            agent_family_id=context.request.agent_family_id,
            primary_decision=response.primary_decision,
            requested_action=response.requested_action,
            question=response.question,
            summary=response.summary,
            reason=response.reason,
            continuation=response.continuation,
            open_work_ref=response.open_work_ref,
            subject_refs=response.subject_refs,
            evidence_refs=evidence_refs,
            decided_at=context.request.requested_at,
            next_wake_kind=response.next_wake_kind,
            next_wake_at=response.next_wake_at,
            model_id=context.model_id,
            prompt_sha256=context.prompt_sha256,
            response_sha256=response_sha256,
        )
    except (ValidationError, ValueError):
        raise InvalidResearchAgentDecisionError(reason="hermes_decision_response_invalid") from None


def _safe_reference(reference: str) -> bool:
    return 1 <= len(reference) <= 160 and all(character.isalnum() or character in "._:-" for character in reference)


__all__ = (
    "ClaudeCliResearchAgentDecisionClient",
    "HermesCliResearchAgentDecisionClient",
    "HermesResearchAgentDecisionResponse",
    "InvalidResearchAgentDecisionError",
    "ResearchAgentDecisionClient",
    "ResearchAgentDecisionParseContext",
    "ResearchAgentDecisionRequest",
    "parse_research_agent_decision",
    "render_research_agent_prompt",
)
