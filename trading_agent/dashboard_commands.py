from __future__ import annotations

import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Literal, assert_never, cast

import anyio
from pydantic import ValidationError

from trading_agent.dashboard_directed_jobs import (
    DirectedJobEvent,
    DirectedJobExecutor,
    DirectedJobKind,
    DirectedJobRequest,
    InvalidDirectedJobError,
    load_directed_events,
)
from trading_agent.dashboard_execution_claims import InteractiveClaimStore
from trading_agent.dashboard_hermes_protocol import (
    MAX_HERMES_RESPONSE_CHARS,
    hermes_directed_argv,
    hermes_interaction_argv,
    parse_directed_plan,
    terminal_hermes_event,
)
from trading_agent.dashboard_hermes_sessions import (
    HermesSessionBindingStore,
    InvalidHermesSessionBindingError,
)
from trading_agent.dashboard_interaction_models import (
    DashboardInteractionMessage,
    InteractionExecution,
    InteractionPayload,
    InteractionResult,
    PairingTicketMessage,
    parse_dashboard_event,
)
from trading_agent.dashboard_outbound_redaction import redact_outbound_text

MAX_RESPONSE_CHARS = MAX_HERMES_RESPONSE_CHARS


async def execute_interaction(
    interaction: InteractionPayload,
    *,
    hermes_executable: Path,
    worktree: Path,
    state_root: Path,
    source_evidence_root: Path,
    timeout_seconds: float = 900,
    environment: Mapping[str, str] | None = None,
    directed_event_sink: Callable[[DirectedJobEvent], Awaitable[None]] | None = None,
) -> InteractionExecution:
    claims = InteractiveClaimStore(state_root / "interactive-claims.sqlite3")
    kind: Literal["conversation", "directed"] = "conversation" if interaction.mode == "conversation" else "directed"
    if not claims.claim(interaction.id, interaction.agent_id, kind):
        return _duplicate_execution(interaction.id, claims, state_root)
    if not claims.mark_running(interaction.id, process_started=True):
        return _failed_execution(interaction.id, "interaction_claim_transition_failed")
    sessions = HermesSessionBindingStore(state_root / "hermes-sessions")
    try:
        session_id = sessions.session_for(interaction.agent_id)
        if interaction.mode == "conversation":
            argv = hermes_interaction_argv(
                str(hermes_executable),
                interaction.agent_id,
                interaction.command,
                session_id,
            )
        else:
            argv = hermes_directed_argv(
                str(hermes_executable),
                interaction.agent_id,
                interaction.command,
                cast(DirectedJobKind, interaction.mode),
                session_id,
            )
        with anyio.fail_after(timeout_seconds):
            completed = await anyio.run_process(
                argv,
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
        if interaction.mode != "conversation":
            plan = parse_directed_plan(
                terminal.text,
                cast(DirectedJobKind, interaction.mode),
            )
            return await _execute_directed(
                interaction,
                plan.intent,
                claims,
                state_root,
                source_evidence_root,
                worktree,
                directed_event_sink,
            )
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


async def _execute_directed(
    interaction: InteractionPayload,
    intent: str,
    claims: InteractiveClaimStore,
    state_root: Path,
    source: Path,
    repository: Path,
    event_sink: Callable[[DirectedJobEvent], Awaitable[None]] | None,
) -> InteractionExecution:
    request = DirectedJobRequest(
        interaction_id=interaction.id,
        agent_family_id=interaction.agent_id,
        job_kind=cast(DirectedJobKind, interaction.mode),
        command=intent,
    )
    try:
        executor = DirectedJobExecutor(
            state_root=state_root / "directed-jobs",
            source_evidence_root=source,
            repository=repository,
        )
        if event_sink is None:
            events = await anyio.to_thread.run_sync(executor.execute, request)
        else:

            def emit(event: DirectedJobEvent) -> None:
                anyio.from_thread.run(event_sink, event)

            events = await anyio.to_thread.run_sync(
                partial(executor.execute, request, emit),
            )
    except (InvalidDirectedJobError, OSError, subprocess.SubprocessError):
        return _terminal_failure(interaction.id, claims, "directed_job_failed", True)
    terminal = events[-1]
    if terminal.kind != "result":
        return _terminal_failure(interaction.id, claims, "directed_text_only_completion_forbidden", True)
    if terminal.state in ("failed", "uncertain"):
        _ = claims.mark_terminal(interaction.id, terminal.state)
        return InteractionExecution(
            InteractionResult(
                interaction_id=interaction.id,
                state=terminal.state,
                response=terminal.summary,
            ),
            events,
            True,
        )
    if terminal.state != "completed" or terminal.result_sha256 is None:
        return _terminal_failure(interaction.id, claims, "directed_text_only_completion_forbidden", True)
    _ = claims.mark_terminal(interaction.id, terminal.state)
    return InteractionExecution(
        InteractionResult(
            interaction_id=interaction.id,
            state="completed",
            response=terminal.summary,
        ),
        events,
        True,
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
    state_root: Path,
) -> InteractionExecution:
    claim = claims.get(interaction_id)
    events: tuple[DirectedJobEvent, ...] = ()
    if claim is not None and claim.kind == "directed":
        try:
            events = load_directed_events(state_root / "directed-jobs", interaction_id)
        except (InvalidDirectedJobError, OSError, ValidationError):
            events = ()
        if (
            events
            and events[-1].kind == "result"
            and events[-1].state
            in (
                "completed",
                "failed",
                "uncertain",
            )
        ):
            terminal_state = cast(Literal["completed", "failed", "uncertain"], events[-1].state)
            if claim.state == "running":
                _ = claims.mark_terminal(interaction_id, terminal_state)
            return InteractionExecution(
                InteractionResult(
                    interaction_id=interaction_id,
                    state=terminal_state,
                    response=events[-1].summary,
                ),
                events,
                False,
            )
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
        events,
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
