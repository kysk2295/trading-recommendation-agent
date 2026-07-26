from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_directed_research import (
    AuthoritativeDirectedResearchBroker,
)
from trading_agent.dashboard_directed_research_models import (
    DirectedResearchBroker,
    DirectedResearchKind,
    InvalidDirectedResearchBrokerError,
    parse_directed_research_receipt,
)
from trading_agent.dashboard_outbound_redaction import require_safe_outbound_text

MAX_EVENT_LOG_BYTES = 64 * 1024
DirectedJobKind = Literal["research", "analysis", "hypothesis", "experiment", "allowed_code"]
DirectedEventKind = Literal["progress", "evidence", "result"]
DirectedState = Literal["running", "completed", "failed", "uncertain", "blocked"]
DirectedEventSink = Callable[["DirectedJobEvent"], None]


class InvalidDirectedJobError(RuntimeError):
    pass


class DirectedJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    agent_family_id: AgentFamilyId
    job_kind: DirectedJobKind
    command: str = Field(min_length=1, max_length=2_000)


class DirectedJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["directed_job_event"] = "directed_job_event"
    interaction_id: str
    agent_family_id: AgentFamilyId
    job_kind: DirectedJobKind
    kind: DirectedEventKind
    state: DirectedState
    sequence: int = Field(ge=0, le=32)
    step: str | None = Field(default=None, max_length=40)
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    result_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    summary: str | None = Field(default=None, max_length=240)


class DirectedJobExecutor:
    def __init__(
        self,
        *,
        state_root: Path,
        source_evidence_root: Path,
        repository: Path,
        research_broker: DirectedResearchBroker | None = None,
    ) -> None:
        self._state_root = state_root
        self._repository = repository
        self._research_broker = research_broker or AuthoritativeDirectedResearchBroker(
            state_root=state_root,
            source_evidence_root=source_evidence_root,
        )

    def execute(
        self,
        request: DirectedJobRequest,
        event_sink: DirectedEventSink | None = None,
    ) -> tuple[DirectedJobEvent, ...]:
        root = self._state_root / request.interaction_id
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        progress = self._event(
            request,
            "progress",
            "running",
            0,
            step=_step_name(request.job_kind),
        )
        _record_and_emit(root, progress, event_sink)
        try:
            evidence_sha, result_sha, summary = self._execute_operation(request, root)
        except InvalidDirectedJobError:
            terminal_state: Literal["failed", "uncertain"] = (
                "failed" if request.job_kind == "allowed_code" else "uncertain"
            )
            terminal = self._event(
                request,
                "result",
                terminal_state,
                1,
                summary=f"{request.job_kind} execution {terminal_state}",
            )
            _record_and_emit(root, terminal, event_sink)
            return progress, terminal
        evidence = self._event(
            request,
            "evidence",
            "running",
            1,
            evidence_sha256=evidence_sha,
        )
        _record_and_emit(root, evidence, event_sink)
        result = self._event(
            request,
            "result",
            "completed",
            2,
            result_sha256=result_sha,
            summary=summary,
        )
        _record_and_emit(root, result, event_sink)
        return progress, evidence, result

    def _execute_operation(
        self,
        request: DirectedJobRequest,
        root: Path,
    ) -> tuple[str, str, str]:
        match request.job_kind:
            case "allowed_code":
                return self._code_check(root)
            case "research" | "analysis" | "hypothesis" | "experiment":
                try:
                    raw = self._research_broker.execute(request.job_kind, request.agent_family_id)
                    receipt = parse_directed_research_receipt(raw, request.job_kind)
                except (
                    InvalidDirectedResearchBrokerError,
                    OSError,
                    TimeoutError,
                ) as error:
                    raise InvalidDirectedJobError("directed_research_broker_failed") from error
                _write_bytes_once(root / "broker-receipt.json", raw)
                receipt_sha = hashlib.sha256(raw).hexdigest()
                return receipt_sha, receipt.result_sha256, receipt.summary

    def _code_check(self, root: Path) -> tuple[str, str, str]:
        try:
            completed = subprocess.run(
                ("/usr/bin/git", "diff", "--check", "--"),
                cwd=self._repository,
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise InvalidDirectedJobError("directed_code_check_failed") from error
        if completed.returncode != 0:
            raise InvalidDirectedJobError("directed_code_check_failed")
        payload = json.dumps(
            {
                "operation": "code_check",
                "returncode": completed.returncode,
                "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        _write_bytes_once(root / "code-check-receipt.json", payload)
        result_sha = hashlib.sha256(payload).hexdigest()
        return result_sha, result_sha, "allowlisted repository check completed"

    @staticmethod
    def _event(
        request: DirectedJobRequest,
        kind: DirectedEventKind,
        state: DirectedState,
        sequence: int,
        **values: str | None,
    ) -> DirectedJobEvent:
        summary = values.get("summary")
        if summary is not None:
            require_safe_outbound_text(summary)
        return DirectedJobEvent(
            interaction_id=request.interaction_id,
            agent_family_id=request.agent_family_id,
            job_kind=request.job_kind,
            kind=kind,
            state=state,
            sequence=sequence,
            step=values.get("step"),
            evidence_sha256=values.get("evidence_sha256"),
            result_sha256=values.get("result_sha256"),
            summary=summary,
        )


def _step_name(kind: DirectedJobKind) -> str:
    match kind:
        case "research" | "analysis" | "hypothesis" | "experiment":
            operation: DirectedResearchKind = kind
            return f"{operation}_broker"
        case "allowed_code":
            return "code_check"


def _write_bytes_once(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record_and_emit(
    root: Path,
    event: DirectedJobEvent,
    event_sink: DirectedEventSink | None,
) -> None:
    payload = event.model_dump_json().encode() + b"\n"
    descriptor = os.open(
        root / "events.jsonl",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if event_sink is not None:
        event_sink(event)


def load_directed_events(state_root: Path, interaction_id: str) -> tuple[DirectedJobEvent, ...]:
    path = state_root / interaction_id / "events.jsonl"
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        payload = os.read(descriptor, MAX_EVENT_LOG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_EVENT_LOG_BYTES:
        raise InvalidDirectedJobError("directed_event_log_oversized")
    return tuple(DirectedJobEvent.model_validate_json(line) for line in payload.splitlines() if line.strip())


__all__ = (
    "DirectedEventSink",
    "DirectedJobEvent",
    "DirectedJobExecutor",
    "DirectedJobRequest",
    "InvalidDirectedJobError",
    "load_directed_events",
)
