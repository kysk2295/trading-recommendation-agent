from __future__ import annotations

import datetime as dt
import functools
import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType
from typing import assert_never

from trading_agent._autonomous_supervisor_process import AutonomousExecutionError
from trading_agent.autonomous_reasoning import AutonomousReasoningClient
from trading_agent.autonomous_reasoning_codec import AutonomousStructuredReasoner
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolRuntime
from trading_agent.researcher_llm import FixtureLlmProposalClient, HermesCliProposalClient

type Primitive = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FixtureReasonerWire:
    response: bytes
    model_id: str
    seed: int | None
    temperature: float


@dataclass(frozen=True, slots=True)
class HermesReasonerWire:
    executable: str
    model_id: str
    provider_id: str
    seed: int | None
    temperature: float
    timeout_seconds: float


type ReasonerWire = FixtureReasonerWire | HermesReasonerWire


@dataclass(frozen=True, slots=True)
class FunctionWire:
    module: str
    qualname: str
    bound: tuple[tuple[str, Primitive], ...] = ()


@dataclass(frozen=True, slots=True)
class BindingWire:
    name: str
    allowed_roles: tuple[str, ...]
    allowed_arguments: tuple[str, ...]
    invoke: FunctionWire
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolRuntimeWire:
    bindings: tuple[BindingWire, ...]
    clock: FunctionWire
    worker_modules: frozenset[str]


def reasoner_wire(reasoner: AutonomousReasoningClient) -> ReasonerWire:
    if type(reasoner) is not AutonomousStructuredReasoner:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    client = object.__getattribute__(reasoner, "client")
    if type(client) is FixtureLlmProposalClient:
        if len(client.response) > 32_768:
            raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
        return FixtureReasonerWire(client.response, client.model_id, client.seed, client.temperature)
    if type(client) is HermesCliProposalClient:
        if any(len(value) > 4_096 for value in (str(client.executable), client.model_id, client.provider_id)):
            raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
        return HermesReasonerWire(
            str(client.executable),
            client.model_id,
            client.provider_id,
            client.seed,
            client.temperature,
            client.timeout_seconds,
        )
    raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")


def build_reasoner(wire: ReasonerWire) -> AutonomousStructuredReasoner:
    match wire:
        case FixtureReasonerWire(response, model_id, seed, temperature):
            client = FixtureLlmProposalClient(response, model_id, seed, temperature)
        case HermesReasonerWire(executable, model_id, provider_id, seed, temperature, timeout_seconds):
            client = HermesCliProposalClient(
                Path(executable), model_id, provider_id, seed, temperature, timeout_seconds
            )
        case unreachable:
            assert_never(unreachable)
    return AutonomousStructuredReasoner(client)


def tools_wire(tools: AutonomousToolRuntime) -> ToolRuntimeWire:
    if type(tools) is not AutonomousToolRuntime:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    bindings = object.__getattribute__(tools, "_bindings")
    clock = object.__getattribute__(tools, "_clock")
    worker_modules = object.__getattribute__(tools, "_worker_modules")
    if len(bindings) > 16 or len(worker_modules) > 16:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    return ToolRuntimeWire(
        tuple(_binding_wire(binding, worker_modules) for _, binding in sorted(bindings.items())),
        _function_wire(clock, worker_modules),
        worker_modules,
    )


def build_tools(wire: ToolRuntimeWire) -> AutonomousToolRuntime:
    from trading_agent.autonomous_task_models import AutonomousAgentRole

    return AutonomousToolRuntime(
        tuple(
            AutonomousToolBinding(
                binding.name,
                frozenset(AutonomousAgentRole(role) for role in binding.allowed_roles),
                frozenset(binding.allowed_arguments),
                _function(binding.invoke, wire.worker_modules),
                binding.evidence_refs,
            )
            for binding in wire.bindings
        ),
        _function(wire.clock, wire.worker_modules),
        worker_modules=wire.worker_modules,
    )


def _binding_wire(binding: AutonomousToolBinding, worker_modules: frozenset[str]) -> BindingWire:
    if type(binding) is not AutonomousToolBinding:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    return BindingWire(
        binding.name,
        tuple(sorted(role.value for role in binding.allowed_roles)),
        tuple(sorted(binding.allowed_arguments)),
        _function_wire(binding.invoke, worker_modules),
        binding.evidence_refs,
    )


def _function_wire(callback: Callable[..., str | dt.datetime], worker_modules: frozenset[str]) -> FunctionWire:
    bound: tuple[tuple[str, Primitive], ...] = ()
    if type(callback) is functools.partial:
        function = object.__getattribute__(callback, "func")
        arguments = object.__getattribute__(callback, "args")
        keywords = object.__getattribute__(callback, "keywords") or {}
        if arguments or any(type(value) not in {str, int, float, bool, type(None)} for value in keywords.values()):
            raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
        if len(keywords) > 8 or any(len(value) > 4_096 for value in keywords.values() if type(value) is str):
            raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
        bound = tuple(sorted(keywords.items()))
    else:
        function = callback
    if type(function) is not FunctionType:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    module = object.__getattribute__(function, "__module__")
    qualname = object.__getattribute__(function, "__qualname__")
    loaded = sys.modules.get(module)
    if loaded is None or module not in worker_modules or "." in qualname or vars(loaded).get(qualname) is not function:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    return FunctionWire(module, qualname, bound)


def _function(wire: FunctionWire, worker_modules: frozenset[str]):
    if wire.module not in worker_modules or "." in wire.qualname:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    function = vars(importlib.import_module(wire.module)).get(wire.qualname)
    if type(function) is not FunctionType:
        raise AutonomousExecutionError(reason="autonomous_execution_boundary_unsupported")
    return functools.partial(function, **dict(wire.bound)) if wire.bound else function


__all__ = ("ReasonerWire", "ToolRuntimeWire", "build_reasoner", "build_tools", "reasoner_wire", "tools_wire")
