from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, assert_never, cast

import anyio
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_directed_jobs import (
    DirectedJobEvent,
    DirectedJobExecutor,
    DirectedJobKind,
    DirectedJobRequest,
    InvalidDirectedJobError,
)
from trading_agent.dashboard_execution_claims import InteractiveClaimStore
from trading_agent.dashboard_hermes_protocol import (
    MAX_HERMES_RESPONSE_CHARS,
    hermes_interaction_argv,
    terminal_hermes_event,
)
from trading_agent.dashboard_hermes_sessions import (
    HermesSessionBindingStore,
    InvalidHermesSessionBindingError,
)
from trading_agent.dashboard_outbound_redaction import redact_outbound_text

InteractionMode = Literal[
    "conversation",
    "research",
    "analysis",
    "hypothesis",
    "experiment",
    "allowed_code",
]
MAX_RESPONSE_CHARS = MAX_HERMES_RESPONSE_CHARS


class InteractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    agent_id: AgentFamilyId
    mode: InteractionMode
    command: str = Field(min_length=1, max_length=2_000)
    state: Literal["queued", "running"]
    response: None
    created_at: datetime
    updated_at: datetime


class DashboardInteractionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction"]
    interaction: InteractionPayload


class PairingTicketMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["pairing_ticket"]
    path: str = Field(pattern=r"^/operator/pair/[A-Za-z0-9_-]{40,}$")


type DashboardEvent = Annotated[
    DashboardInteractionMessage | PairingTicketMessage,
    Field(discriminator="type"),
]


class InteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction_result"] = "interaction_result"
    interaction_id: str
    state: Literal["running", "completed", "failed", "uncertain"]
    response: str | None = Field(max_length=MAX_RESPONSE_CHARS)


@dataclass(frozen=True, slots=True)
class InteractionExecution:
    result: InteractionResult
    directed_events: tuple[DirectedJobEvent, ...]
    process_started: bool


_event_adapter = TypeAdapter(DashboardEvent)


def parse_dashboard_event(raw: str) -> DashboardEvent:
    return _event_adapter.validate_json(raw)


async def execute_interaction(
    interaction: InteractionPayload,
    *,
    hermes_executable: Path,
    worktree: Path,
    state_root: Path,
    source_evidence_root: Path,
    timeout_seconds: float = 900,
    environment: Mapping[str, str] | None = None,
) -> InteractionExecution:
    claims = InteractiveClaimStore(state_root / "interactive-claims.sqlite3")
    kind: Literal["conversation", "directed"] = "conversation" if interaction.mode == "conversation" else "directed"
    if not claims.claim(interaction.id, interaction.agent_id, kind):
        return _duplicate_execution(interaction.id, claims)
    if interaction.mode != "conversation":
        return _execute_directed(interaction, claims, state_root, source_evidence_root, worktree)
    if not claims.mark_running(interaction.id):
        return _failed_execution(interaction.id, "interaction_claim_transition_failed")
    sessions = HermesSessionBindingStore(state_root / "hermes-sessions")
    try:
        session_id = sessions.session_for(interaction.agent_id)
        with anyio.fail_after(timeout_seconds):
            completed = await anyio.run_process(
                hermes_interaction_argv(
                    str(hermes_executable),
                    interaction.agent_id,
                    interaction.command,
                    session_id,
                ),
                cwd=worktree,
                env={**os.environ, **({} if environment is None else environment)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        terminal = terminal_hermes_event(completed.stdout)
        if completed.returncode != 0 or terminal.failed:
            return _terminal_failure(interaction.id, claims, "hermes_execution_failed", True)
        if session_id is None:
            sessions.capture(interaction.agent_id, terminal.session_id)
        elif terminal.session_id != session_id:
            return _terminal_failure(interaction.id, claims, "hermes_resume_session_changed", True)
        response = redact_outbound_text(terminal.text, max_chars=MAX_RESPONSE_CHARS)
        _ = claims.mark_terminal(interaction.id, "completed")
        return InteractionExecution(
            InteractionResult(interaction_id=interaction.id, state="completed", response=response),
            (),
            True,
        )
    except TimeoutError:
        return _terminal_failure(interaction.id, claims, "interaction_timeout", True)
    except (OSError, ValidationError, InvalidHermesSessionBindingError):
        return _terminal_failure(interaction.id, claims, "hermes_protocol_failed", True)


def _execute_directed(
    interaction: InteractionPayload,
    claims: InteractiveClaimStore,
    state_root: Path,
    source: Path,
    repository: Path,
) -> InteractionExecution:
    if not claims.mark_running(interaction.id, process_started=False):
        return _failed_execution(interaction.id, "interaction_claim_transition_failed")
    request = DirectedJobRequest(
        interaction_id=interaction.id,
        agent_family_id=interaction.agent_id,
        job_kind=cast(DirectedJobKind, interaction.mode),
        command=interaction.command,
    )
    try:
        events = DirectedJobExecutor(
            state_root=state_root / "directed-jobs",
            source_evidence_root=source,
            repository=repository,
        ).execute(request)
    except (InvalidDirectedJobError, OSError, subprocess.SubprocessError):
        return _terminal_failure(interaction.id, claims, "directed_job_failed", False)
    terminal = events[-1]
    if terminal.kind != "result" or terminal.state != "completed" or terminal.result_sha256 is None:
        return _terminal_failure(interaction.id, claims, "directed_text_only_completion_forbidden", False)
    _ = claims.mark_terminal(interaction.id, "completed")
    return InteractionExecution(
        InteractionResult(
            interaction_id=interaction.id,
            state="completed",
            response=terminal.summary,
        ),
        events,
        False,
    )


def _terminal_failure(
    interaction_id: str,
    claims: InteractiveClaimStore,
    reason: str,
    process_started: bool,
) -> InteractionExecution:
    _ = claims.mark_terminal(interaction_id, "failed")
    return InteractionExecution(
        InteractionResult(interaction_id=interaction_id, state="failed", response=reason),
        (),
        process_started,
    )


def _failed_execution(interaction_id: str, reason: str) -> InteractionExecution:
    return InteractionExecution(
        InteractionResult(interaction_id=interaction_id, state="failed", response=reason),
        (),
        False,
    )


def _duplicate_execution(
    interaction_id: str,
    claims: InteractiveClaimStore,
) -> InteractionExecution:
    claim = claims.get(interaction_id)
    if claim is None:
        state: Literal["completed", "failed", "uncertain"] = "uncertain"
    else:
        match claim.state:
            case "queued" | "running" | "uncertain":
                state = "uncertain"
            case "completed" | "failed":
                state = claim.state
            case unexpected:
                assert_never(unexpected)
    return InteractionExecution(
        InteractionResult(interaction_id=interaction_id, state=state, response=None),
        (),
        False,
    )


__all__ = (
    "DashboardInteractionMessage",
    "InteractionExecution",
    "InteractionPayload",
    "InteractionResult",
    "PairingTicketMessage",
    "execute_interaction",
    "parse_dashboard_event",
)
