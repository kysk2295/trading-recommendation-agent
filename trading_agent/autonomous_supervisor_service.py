from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from trading_agent._autonomous_supervisor_steps import SourceAdmissionPayload, safe_payload
from trading_agent.autonomous_browser_tools import BrowserToolServices, browser_bindings
from trading_agent.autonomous_kr_service_builder import kr_tool_services_for_config
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices, kr_tool_bindings
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_reasoning_codec import AutonomousStructuredReasoner
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_supervisor_status import (
    AutonomousSupervisorPaths,
    AutonomousSupervisorStatus,
    autonomous_supervisor_paths,
    autonomous_supervisor_status,
    autonomous_supervisor_status_for_config,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolExecutionContext,
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
_ALL_ROLES: Final = frozenset(AutonomousAgentRole)


class InvalidAutonomousSupervisorServiceError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def utc_clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def configured_proposal_client(config: SystematicResearchActionConfig) -> LlmProposalClient:
    if config.response_fixture is not None:
        return FixtureLlmProposalClient(load_private_canonical_llm_response(config.response_fixture))
    if config.hermes_executable is None:
        raise InvalidAutonomousSupervisorServiceError(reason="autonomous_llm_provider_missing")
    return HermesCliProposalClient(config.hermes_executable, config.model_id, config.provider_id)


def evidence_read_tool(
    _args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    task_database: str,
) -> str:
    store = AutonomousTaskStore(Path(task_database))
    try:
        admissions = tuple(
            payload.evidence_json
            for step in store.reader().steps(context.task_id)
            if isinstance(payload := safe_payload(step), SourceAdmissionPayload)
        )
    finally:
        store.close()
    evidence = () if not admissions else (json.loads(admissions[-1]),)
    return _canonical({"evidence": evidence})


def memory_search_tool(
    args: AutonomousToolArguments,
    _context: AutonomousToolExecutionContext,
    *,
    memory_database: str,
) -> str:
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


def task_history_tool(
    _args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    task_database: str,
) -> str:
    store = AutonomousTaskStore(Path(task_database))
    try:
        steps = store.reader().steps(context.task_id)[-32:]
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
    *,
    browser: BrowserToolServices | None = None,
    kr: KrAutonomousToolServices | None = None,
) -> AutonomousToolRuntime:
    foundation_bindings = (
        _binding("evidence.read", frozenset(), evidence_read_tool, "task_database", tasks.path),
        _binding(
            "memory.search",
            frozenset({"scope", "subject_ref"}),
            memory_search_tool,
            "memory_database",
            memories.path,
        ),
        _binding("task.history", frozenset(), task_history_tool, "task_database", tasks.path),
    )
    browser_bindings_ = () if browser is None else browser_bindings(browser)
    kr_bindings = () if kr is None else kr_tool_bindings(kr)
    bindings = (*foundation_bindings, *browser_bindings_, *kr_bindings)
    worker_modules = frozenset(
        {
            _WORKER_MODULE,
            *(
                ()
                if browser is None
                else ("trading_agent.autonomous_browser_tools", "trading_agent.autonomous_browser_tool_actions")
            ),
            *(() if kr is None else ("trading_agent.autonomous_kr_tools",)),
        }
    )
    return AutonomousToolRuntime(
        tuple(sorted(bindings, key=lambda item: item.name)), utc_clock, worker_modules=worker_modules
    )


def build_autonomous_supervisor(
    config: ResearchAgentServiceConfig,
    *,
    client: LlmProposalClient | None = None,
    clock: Callable[[], dt.datetime] = utc_clock,
    browser: BrowserToolServices | None = None,
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
    kr = kr_tool_services_for_config(config, paths.task_database, clock)
    runtime = AutonomousSupervisorRuntime(
        tasks,
        memories,
        AutonomousStructuredReasoner(proposal_client),
        build_foundation_tool_runtime(tasks, memories, browser=browser, kr=kr),
        clock,
        time.monotonic,
    )
    return AutonomousSupervisorAdapter(runtime)


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
