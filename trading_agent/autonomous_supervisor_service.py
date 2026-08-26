from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent._autonomous_supervisor_steps import SourceAdmissionPayload, safe_payload
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_reasoning_codec import AutonomousStructuredReasoner
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousTaskId,
    AutonomousTaskState,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolInvocationError,
    AutonomousToolRuntime,
)
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_systematic import SystematicResearchActionConfig
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    LlmProposalClient,
    load_private_canonical_llm_response,
)

_WORKER_MODULE: Final = "trading_agent.autonomous_supervisor_service"
_NONTERMINAL: Final = frozenset(AutonomousTaskState) - frozenset(
    {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}
)
_ALL_ROLES: Final = frozenset(AutonomousAgentRole)


class InvalidAutonomousSupervisorServiceError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorPaths:
    task_database: Path
    memory_database: Path


class AutonomousSupervisorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: Literal[True] = True
    total_tasks: int = Field(ge=0)
    nonterminal_tasks: int = Field(ge=0)
    blocked_tasks: int = Field(ge=0)
    next_wake_at: AwareDatetime | None
    last_task_id: AutonomousTaskId | None


def utc_clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def autonomous_supervisor_paths(config: ResearchAgentServiceConfig) -> AutonomousSupervisorPaths:
    root = config.output_root / "autonomous-supervisor"
    return AutonomousSupervisorPaths(root / "tasks.sqlite3", root / "memory.sqlite3")


def configured_proposal_client(config: SystematicResearchActionConfig) -> LlmProposalClient:
    if config.response_fixture is not None:
        return FixtureLlmProposalClient(load_private_canonical_llm_response(config.response_fixture))
    if config.hermes_executable is None:
        raise InvalidAutonomousSupervisorServiceError(reason="autonomous_llm_provider_missing")
    return HermesCliProposalClient(config.hermes_executable, config.model_id, config.provider_id)


def evidence_read_tool(args: AutonomousToolArguments, *, task_database: str) -> str:
    task_id = args.root.get("task_id")
    if task_id is None:
        raise AutonomousToolInvocationError(reason="evidence_task_id_missing")
    store = AutonomousTaskStore(Path(task_database))
    try:
        admissions = tuple(
            payload.evidence_json
            for step in store.reader().steps(task_id)
            if isinstance(payload := safe_payload(step), SourceAdmissionPayload)
        )
    finally:
        store.close()
    evidence = () if not admissions else (json.loads(admissions[-1]),)
    return _canonical({"evidence": evidence})


def memory_search_tool(args: AutonomousToolArguments, *, memory_database: str) -> str:
    scope = args.root.get("scope")
    subject_ref = args.root.get("subject_ref")
    if scope is None or subject_ref is None:
        raise AutonomousToolInvocationError(reason="memory_search_arguments_missing")
    store = AutonomousMemoryStore(Path(memory_database))
    try:
        records = store.reader().search(scope, (subject_ref,), limit=16)
    finally:
        store.close()
    projected = tuple(
        {
            "memory_id": record.memory_id,
            "memory_key": record.memory_key,
            "recorded_at": record.recorded_at.isoformat(),
            "scope": record.scope.value,
            "subject_refs": record.subject_refs,
            "summary": record.summary[:500],
            "version": record.version,
        }
        for record in records
    )
    return _canonical({"memories": projected})


def task_history_tool(args: AutonomousToolArguments, *, task_database: str) -> str:
    task_id = args.root.get("task_id")
    if task_id is None:
        raise AutonomousToolInvocationError(reason="history_task_id_missing")
    store = AutonomousTaskStore(Path(task_database))
    try:
        steps = store.reader().steps(task_id)[-32:]
    finally:
        store.close()
    projected = tuple(
        {
            "occurred_at": step.occurred_at.isoformat(),
            "payload_sha256": hashlib.sha256(step.payload_json.encode()).hexdigest(),
            "role": step.role.value,
            "sequence": step.sequence,
            "state": step.state.value,
            "step_id": step.step_id,
        }
        for step in steps
    )
    return _canonical({"steps": projected})


def build_foundation_tool_runtime(
    tasks: AutonomousTaskStore,
    memories: AutonomousMemoryStore,
) -> AutonomousToolRuntime:
    bindings = (
        _binding("evidence.read", frozenset({"task_id"}), evidence_read_tool, "task_database", tasks.path),
        _binding(
            "memory.search",
            frozenset({"scope", "subject_ref"}),
            memory_search_tool,
            "memory_database",
            memories.path,
        ),
        _binding("task.history", frozenset({"task_id"}), task_history_tool, "task_database", tasks.path),
    )
    return AutonomousToolRuntime(bindings, utc_clock, worker_modules=frozenset({_WORKER_MODULE}))


def build_autonomous_supervisor(
    config: ResearchAgentServiceConfig,
    *,
    client: LlmProposalClient | None = None,
    clock: Callable[[], dt.datetime] = utc_clock,
) -> AutonomousSupervisorAdapter:
    proposal_client = configured_proposal_client(config.systematic) if client is None else client
    paths = autonomous_supervisor_paths(config)
    _prepare_private_root(paths.task_database.parent)
    tasks = AutonomousTaskStore(paths.task_database)
    memories = AutonomousMemoryStore(paths.memory_database)
    with tasks.writer():
        pass
    with memories.writer():
        pass
    runtime = AutonomousSupervisorRuntime(
        tasks,
        memories,
        AutonomousStructuredReasoner(proposal_client),
        build_foundation_tool_runtime(tasks, memories),
        clock,
        time.monotonic,
    )
    return AutonomousSupervisorAdapter(runtime)


def autonomous_supervisor_status(
    tasks: AutonomousTaskStore,
    now: dt.datetime,
) -> AutonomousSupervisorStatus:
    _ = now.astimezone(dt.UTC)
    durable = tasks.reader().tasks()
    open_tasks = tuple(task for task in durable if task.state in _NONTERMINAL)
    wakes = tuple(task.next_wake_at for task in open_tasks if task.next_wake_at is not None)
    latest = max(durable, key=lambda task: (task.updated_at, task.task_id), default=None)
    return AutonomousSupervisorStatus(
        total_tasks=len(durable),
        nonterminal_tasks=len(open_tasks),
        blocked_tasks=sum(task.state is AutonomousTaskState.BLOCKED for task in open_tasks),
        next_wake_at=min(wakes, default=None),
        last_task_id=None if latest is None else latest.task_id,
    )


def autonomous_supervisor_status_for_config(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> AutonomousSupervisorStatus:
    tasks = AutonomousTaskStore(autonomous_supervisor_paths(config).task_database)
    try:
        return autonomous_supervisor_status(tasks, now)
    finally:
        tasks.close()


def _binding(
    name: str,
    arguments: frozenset[str],
    callback: Callable[..., str],
    bound_name: str,
    path: Path,
) -> AutonomousToolBinding:
    invoke = functools.partial(callback, **{bound_name: str(path)})
    return AutonomousToolBinding(name, _ALL_ROLES, arguments, invoke, ())


def _canonical(value: dict[str, tuple[dict[str, str | int | tuple[str, ...]], ...]]) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _prepare_private_root(path: Path) -> None:
    descriptor = open_private_parent(path, create=True)
    try:
        require_private_directory(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "AutonomousSupervisorPaths",
    "AutonomousSupervisorStatus",
    "InvalidAutonomousSupervisorServiceError",
    "autonomous_supervisor_paths",
    "autonomous_supervisor_status",
    "autonomous_supervisor_status_for_config",
    "build_autonomous_supervisor",
    "build_foundation_tool_runtime",
    "configured_proposal_client",
)
