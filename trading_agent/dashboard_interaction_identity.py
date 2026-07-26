from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, assert_never, cast

from pydantic import ValidationError

from trading_agent.dashboard_directed_jobs import (
    DirectedJobEvent,
    InvalidDirectedJobError,
    load_directed_events,
)
from trading_agent.dashboard_execution_claims import InteractiveClaimStore
from trading_agent.dashboard_interaction_models import (
    InteractionExecution,
    InteractionPayload,
    InteractionResult,
)


def interaction_request_sha256(interaction: InteractionPayload) -> str:
    payload = json.dumps(
        {
            "command": interaction.command,
            "mode": interaction.mode,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def duplicate_execution(
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
        if events and events[-1].kind == "result" and events[-1].state in ("completed", "failed", "uncertain"):
            terminal_state = cast(
                Literal["completed", "failed", "uncertain"],
                events[-1].state,
            )
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


__all__ = ("duplicate_execution", "interaction_request_sha256")
