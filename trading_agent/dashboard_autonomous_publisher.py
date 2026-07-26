from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Protocol

import anyio
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from pydantic import ValidationError

from trading_agent.dashboard_agent_control_plane import (
    AutonomousControlPlane,
    AutonomousOutcome,
    AutonomousPolicy,
)
from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
)
from trading_agent.dashboard_worktree_executor import IsolatedWorktreeExecutor
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_AUTONOMOUS_STATE = Path.home() / ".cache" / "trading-agent" / "dashboard-autonomous"
FAKE_HERMES_EXECUTABLE = REPOSITORY / "tests" / "fixtures" / "dashboard" / "fake_hermes"
DEFAULT_AUTONOMOUS_POLICY: Final = AutonomousPolicy(
    max_trigger_age_seconds=900,
    max_daily_tokens_per_family=100_000,
    max_daily_cost_microusd_per_family=10_000_000,
    cooldown_seconds=300,
    max_global_concurrency=1,
    max_family_concurrency=1,
    rolling_failure_window_seconds=86_400,
    max_rolling_failures=3,
)


class AgentTaskEventSocket(Protocol):
    async def send(self, message: str) -> None: ...


class InvalidAutonomousTriggerFixtureError(ValueError):
    pass


def execute_autonomous_fixture(
    trigger_path: Path,
    *,
    state_root: Path,
    hermes_executable: Path,
    fake_hermes: bool,
) -> AutonomousOutcome:
    try:
        trigger = AutonomousTriggerV1.model_validate_json(read_private_text_query_only(trigger_path))
    except (InvalidPrivateQueryFileError, ValidationError) as error:
        raise InvalidAutonomousTriggerFixtureError from error
    return run_autonomous_trigger(
        trigger,
        state_root=state_root,
        hermes_executable=FAKE_HERMES_EXECUTABLE if fake_hermes else hermes_executable,
        receipts=[],
    )


async def stream_autonomous_trigger_event(
    socket: AgentTaskEventSocket,
    trigger_path: Path,
    send_lock: anyio.Lock,
    *,
    hermes_executable: Path,
) -> None:
    try:
        trigger = AutonomousTriggerV1.model_validate_json(read_private_text_query_only(trigger_path))
    except (InvalidPrivateQueryFileError, ValidationError):
        return
    receipts: list[AutonomousTaskReceiptV1] = []
    await run_sync_in_worker_thread(
        lambda: run_autonomous_trigger(
            trigger,
            state_root=DEFAULT_AUTONOMOUS_STATE,
            hermes_executable=hermes_executable,
            receipts=receipts,
        )
    )
    async with send_lock:
        for receipt in receipts:
            await socket.send(
                json.dumps(
                    {"type": "agent_task_event", "task": receipt.model_dump(mode="json")},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )


def autonomous_trigger_paths(changes: frozenset[Path]) -> tuple[Path, ...]:
    candidates: set[Path] = set()
    for path in changes:
        if path.is_file() and path.name.endswith(".autonomous-trigger.json"):
            candidates.add(path)
        elif path.is_dir():
            candidates.update(path.rglob("*.autonomous-trigger.json"))
    return tuple(sorted(candidates))


def run_autonomous_trigger(
    trigger: AutonomousTriggerV1,
    *,
    state_root: Path,
    hermes_executable: Path,
    receipts: list[AutonomousTaskReceiptV1],
) -> AutonomousOutcome:
    plane = AutonomousControlPlane(
        state_root=state_root,
        executor=IsolatedWorktreeExecutor(
            repository=REPOSITORY,
            environment_root=state_root / "environments",
            hermes_executable=hermes_executable,
        ),
        policy=DEFAULT_AUTONOMOUS_POLICY,
        event_sink=receipts.append,
    )
    return plane.handle(trigger)


__all__ = (
    "DEFAULT_AUTONOMOUS_STATE",
    "InvalidAutonomousTriggerFixtureError",
    "autonomous_trigger_paths",
    "execute_autonomous_fixture",
    "stream_autonomous_trigger_event",
)
