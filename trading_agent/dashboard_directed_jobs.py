from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_outbound_redaction import require_safe_outbound_text

DirectedJobKind = Literal["research", "analysis", "hypothesis", "experiment", "allowed_code"]
DirectedEventKind = Literal["progress", "evidence", "result"]
DirectedState = Literal["running", "completed", "failed", "uncertain", "blocked"]

_PLANS: Final[dict[DirectedJobKind, tuple[str, ...]]] = {
    "research": ("evidence_query",),
    "analysis": ("evidence_query", "analysis_digest"),
    "hypothesis": ("evidence_query", "hypothesis_register"),
    "experiment": ("evidence_query", "hypothesis_register", "experiment_run"),
    "allowed_code": ("code_check",),
}


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
    def __init__(self, *, state_root: Path, source_evidence_root: Path, repository: Path) -> None:
        self._state_root = state_root
        self._source = source_evidence_root
        self._repository = repository

    def execute(self, request: DirectedJobRequest) -> tuple[DirectedJobEvent, ...]:
        root = self._state_root / request.interaction_id
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        command_sha = hashlib.sha256(request.command.encode()).hexdigest()
        artifacts: list[str] = []
        events: list[DirectedJobEvent] = []
        for sequence, step in enumerate(_PLANS[request.job_kind]):
            events.append(self._event(request, "progress", "running", sequence, step=step))
            artifacts.append(self._run_step(step, root, request, command_sha))
        evidence_sha = hashlib.sha256("".join(artifacts).encode()).hexdigest()
        _write_once(root / "evidence.json", {"artifact_hashes": artifacts, "evidence_sha256": evidence_sha})
        events.append(
            self._event(
                request,
                "evidence",
                "running",
                len(events),
                evidence_sha256=evidence_sha,
            )
        )
        result_sha = hashlib.sha256(f"{request.interaction_id}:{evidence_sha}".encode()).hexdigest()
        events.append(
            self._event(
                request,
                "result",
                "completed",
                len(events),
                result_sha256=result_sha,
                summary="allowlisted directed job completed with immutable evidence",
            )
        )
        return tuple(events)

    def _run_step(
        self,
        step: str,
        root: Path,
        request: DirectedJobRequest,
        command_sha: str,
    ) -> str:
        if step == "code_check":
            completed = subprocess.run(
                ("/usr/bin/git", "diff", "--check", "--"),
                cwd=self._repository,
                check=False,
                capture_output=True,
                timeout=30,
            )
            if completed.returncode != 0:
                raise InvalidDirectedJobError("directed_code_check_failed")
            payload: Mapping[str, str | int | tuple[str, ...] | list[str]] = {
                "operation": step,
                "returncode": completed.returncode,
            }
        elif step == "evidence_query":
            payload = {"operation": step, "source_hashes": self._source_hashes()}
        else:
            payload = {"command_sha256": command_sha, "operation": step}
        path = root / f"{step}.json"
        _write_once(path, payload)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _source_hashes(self) -> tuple[str, ...]:
        if not self._source.is_dir() or self._source.is_symlink():
            raise InvalidDirectedJobError("directed_source_root_invalid")
        return tuple(
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self._source.glob("*.json"))[:32]
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 256 * 1024
        )

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


def _write_once(
    path: Path,
    payload: Mapping[str, str | int | tuple[str, ...] | list[str]],
) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ("DirectedJobEvent", "DirectedJobExecutor", "DirectedJobRequest", "InvalidDirectedJobError")
