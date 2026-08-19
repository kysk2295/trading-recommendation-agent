from __future__ import annotations

import datetime as dt
import os
import sqlite3
from dataclasses import dataclass
from typing import Literal, assert_never

import anyio
from pydantic import BaseModel, ConfigDict, Field

from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.dashboard_agent_cycle_runtime import read_cycle_runtime_observations
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.day_discovery_loop import (
    DayDiscoveryActionExecutor,
    DayDiscoveryLoop,
    DayDiscoveryLoopConfig,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_execution import GeneratedStrategyLimits
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_actions import (
    ResearchAgentActionConfig,
    ResearchAgentActionContext,
    ResearchAgentActionExecutor,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_store_codec import latest_cycles_from_rows, result_from_payload
from trading_agent.research_agent_day_actions import DayResearchActionExecutor
from trading_agent.research_agent_decision import (
    ClaudeCliResearchAgentDecisionClient,
    HermesCliResearchAgentDecisionClient,
    ResearchAgentDecisionClient,
)
from trading_agent.research_agent_derivatives_actions import DerivativesResearchActionExecutor
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_primary_actions import (
    MarketContextResearchActionExecutor,
    OpportunityResearchActionExecutor,
)
from trading_agent.research_agent_runtime import (
    ConfiguredResearchAgentEvidenceCollector,
    ResearchAgentBoundedCycleResult,
    ResearchAgentRuntime,
    ResearchAgentRuntimeServices,
    ResearchAgentTickResult,
)
from trading_agent.research_agent_runtime_lease import research_agent_runtime_lease
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
)
from trading_agent.research_agent_service_health import (
    health_for_service_report,
    write_persisted_research_agent_service_health,
)
from trading_agent.research_agent_swing_actions import SwingResearchActionExecutor
from trading_agent.research_agent_systematic import SystematicResearchActionExecutor
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    load_systematic_input_activation,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    StructuredHypothesisGenerator,
    load_researcher_context_input,
)
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
    build_researcher_context,
    build_source_hypothesis_factory,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore
from trading_agent.strategy_research_work_sink import PrivateStrategyResearchWorkSink


class ResearchAgentFamilyRuntimeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    agent_family_id: AgentFamilyId
    cursor: int = Field(ge=0)
    cycle_id: str | None
    cycle_state: ResearchAgentCycleState | None
    result_status: ResearchAgentResultStatus | None
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None


class DayDiscoveryMarketRuntimeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    market_id: MarketId
    cursor: str | None
    terminal_failure: str | None


def day_discovery_market_runtime(
    ledger: ExperimentLedgerStore,
) -> tuple[DayDiscoveryMarketRuntimeReport, ...]:
    reader = ledger.reader()
    reports: list[DayDiscoveryMarketRuntimeReport] = []
    for market in MarketId:
        versions = reader.day_hypothesis_versions(market_id=market)
        latest = max(versions, key=lambda item: item.version.created_at, default=None)
        attempts = (
            ()
            if latest is None
            else reader.day_attempts_for_review(market, latest.version.hypothesis_version_id)
        )
        terminal = max(
            attempts,
            key=lambda item: item.attempt.finished_at or item.attempt.started_at,
            default=None,
        )
        reports.append(
            DayDiscoveryMarketRuntimeReport(
                market_id=market,
                cursor=None if latest is None else latest.version.hypothesis_version_id,
                terminal_failure=None if terminal is None else terminal.attempt.error_class,
            )
        )
    return tuple(reports)


class ResearchAgentServiceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["tick", "run", "status"]
    status: str
    agent_family_id: str | None
    cycle_id: str | None
    result_status: str | None
    model_calls: Literal[0, 1]
    recovered_cycles: int
    projected_results: int
    systematic_input_status: Literal["ready", "blocked"]
    systematic_input_sha256: str | None
    systematic_foundation_sha256: str | None
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...]
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None
    broker_mutation: Literal[0] = 0
    observed_at: dt.datetime


class ResearchAgentServiceCycleReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["cycle"] = "cycle"
    status: Literal["idle", "partial", "complete"]
    outcomes: tuple[ResearchAgentTickResult, ...]
    family_count: int
    model_calls: int
    recovered_cycles: int
    projected_results: int
    systematic_input_status: Literal["ready", "blocked"]
    systematic_input_sha256: str | None
    systematic_foundation_sha256: str | None
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...]
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None
    broker_mutation: Literal[0] = 0
    observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SystematicInputReportBinding:
    status: Literal["ready", "blocked"]
    input_sha256: str | None
    foundation_sha256: str | None


class InvalidResearchAgentServiceRuntimeError(RuntimeError):
    pass


def run_service_tick(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    systematic = _systematic_input_report(config)
    runtime = build_service_runtime(config)
    try:
        tick = runtime.tick(now)
        projected = _project_results(config, runtime)
        family_runtime, next_wake_kind, next_wake_at = _runtime_report_from_store(runtime.store)
        report = _report(
            canonical_research_agent_service_config_sha256(config),
            "tick",
            tick,
            projected,
            systematic,
            family_runtime,
            next_wake_kind,
            next_wake_at,
            now,
        )
        write_service_report(config, report)
        return report
    finally:
        runtime.close()


def run_service_cycle(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceCycleReport:
    systematic = _systematic_input_report(config)
    runtime = build_service_runtime(config)
    try:
        cycle = runtime.cycle(now)
        projected = _project_results(config, runtime)
        family_runtime, next_wake_kind, next_wake_at = _runtime_report_from_store(runtime.store)
        report = _cycle_report(
            canonical_research_agent_service_config_sha256(config),
            cycle,
            projected,
            systematic,
            family_runtime,
            next_wake_kind,
            next_wake_at,
            now,
        )
        write_service_report(config, report)
        return report
    finally:
        runtime.close()


async def run_service_forever(
    config: ResearchAgentServiceConfig,
    *,
    tick_seconds: float = 30.0,
) -> None:
    if tick_seconds <= 0:
        raise InvalidResearchAgentServiceRuntimeError
    _prepare_private_runtime_paths(config)
    lease_path = config.output_root / "research-agent-runtime.lock"
    with research_agent_runtime_lease(lease_path):
        runtime = build_service_runtime(config)
        try:
            while True:
                now = dt.datetime.now(dt.UTC)
                systematic = _systematic_input_report(config)
                tick = runtime.tick(now)
                projected = _project_results(config, runtime)
                family_runtime, next_wake_kind, next_wake_at = _runtime_report_from_store(runtime.store)
                write_service_report(
                    config,
                    _report(
                        canonical_research_agent_service_config_sha256(config),
                        "run",
                        tick,
                        projected,
                        systematic,
                        family_runtime,
                        next_wake_kind,
                        next_wake_at,
                        now,
                    ),
                )
                await anyio.sleep(tick_seconds)
        finally:
            runtime.close()


def service_status(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    systematic = _systematic_input_report(config)
    if not config.cycle_database.exists():
        return ResearchAgentServiceReport(
            config_sha256=canonical_research_agent_service_config_sha256(config),
            operation="status",
            status="unavailable",
            agent_family_id=None,
            cycle_id=None,
            result_status=None,
            model_calls=0,
            recovered_cycles=0,
            projected_results=0,
            systematic_input_status=systematic.status,
            systematic_input_sha256=systematic.input_sha256,
            systematic_foundation_sha256=systematic.foundation_sha256,
            family_runtime=tuple(
                ResearchAgentFamilyRuntimeReport(
                    agent_family_id=family,
                    cursor=0,
                    cycle_id=None,
                    cycle_state=None,
                    result_status=None,
                    next_wake_kind=None,
                    next_wake_at=None,
                )
                for family in PRIMARY_AGENT_FAMILIES
            ),
            next_wake_kind=None,
            next_wake_at=None,
            observed_at=now,
        )
    observations = read_cycle_runtime_observations(config.cycle_database)
    family_runtime, next_wake_kind, next_wake_at = _runtime_report_from_database(config.cycle_database)
    latest = max(observations, key=lambda item: item.observed_at, default=None)
    projected = (
        sum(
            event.kind is HermesDeliveryKind.RESEARCH for event in HermesDeliveryReader(config.hermes_database).events()
        )
        if config.hermes_database.exists()
        else 0
    )
    return ResearchAgentServiceReport(
        config_sha256=canonical_research_agent_service_config_sha256(config),
        operation="status",
        status="armed" if latest is None else latest.state,
        agent_family_id=None if latest is None else latest.family,
        cycle_id=None,
        result_status=None if latest is None else latest.state,
        model_calls=0,
        recovered_cycles=0,
        projected_results=projected,
        systematic_input_status=systematic.status,
        systematic_input_sha256=systematic.input_sha256,
        systematic_foundation_sha256=systematic.foundation_sha256,
        family_runtime=family_runtime,
        next_wake_kind=next_wake_kind,
        next_wake_at=next_wake_at,
        observed_at=now,
    )


def build_service_runtime(config: ResearchAgentServiceConfig) -> ResearchAgentRuntime:
    _prepare_private_runtime_paths(config)
    store = ResearchAgentCycleStore(config.cycle_database)
    systematic = SystematicResearchActionExecutor(config.systematic, prior_results=store.results)
    opportunity = OpportunityResearchActionExecutor(
        hypothesis_creator=build_source_hypothesis_factory(
            store.all_evidence,
            config.source_paths.kr_calendar_store,
        ),
        hypothesis_sink=PrivateStrategyResearchWorkSink(
            ExperimentLedgerStore(config.source_paths.experiment_ledger),
            config.source_paths.outputs_root / "strategy-research" / "work",
        ),
    )
    context = MarketContextResearchActionExecutor(store.results)
    day = DayResearchActionExecutor(
        config.source_paths.day_session_root,
        discovery=_ConfiguredDayDiscoveryAction(config),
    )
    swing = SwingResearchActionExecutor(config.source_paths.swing_shadow_database)
    derivatives = DerivativesResearchActionExecutor(store.results)
    actions = ResearchAgentActionExecutor(
        ResearchAgentActionConfig(
            systematic=systematic,
            opportunity=opportunity,
            market_context=context,
            day=day,
            swing=swing,
            derivatives=derivatives,
        )
    )
    services = ResearchAgentRuntimeServices(
        store=store,
        collector=ConfiguredResearchAgentEvidenceCollector(
            config.source_paths,
            systematic_review_root=config.systematic.review_root,
        ),
        decisions=_decision_client(config),
        actions=actions,
    )
    return ResearchAgentRuntime(services)


def _day_discovery_executor(
    config: ResearchAgentServiceConfig, called_at: dt.datetime
) -> DayDiscoveryActionExecutor:
    systematic = config.systematic
    receipts = ResearcherReceiptStore(systematic.receipt_root)
    ledger = ExperimentLedgerStore(config.source_paths.experiment_ledger)
    if systematic.response_fixture is not None:
        proposal_client = FixtureLlmProposalClient(systematic.response_fixture.read_bytes())
    elif systematic.hermes_executable is not None:
        proposal_client = HermesCliProposalClient(
            systematic.hermes_executable, systematic.model_id, systematic.provider_id
        )
    else:
        raise InvalidResearchAgentServiceRuntimeError
    runtime = resolve_generated_strategy_runtime(systematic.python_executable)
    strategies = GeneratedStrategyArtifactStore(systematic.strategy_root, runtime)
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            StructuredHypothesisGenerator(
                proposal_client, receipts, lambda: called_at
            ),
            DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(ledger, receipts, strategies),
        ResearcherPipelineArtifacts(systematic.manifest_root, systematic.queue_root),
    )
    source = load_researcher_context_input(systematic.context)
    return DayDiscoveryActionExecutor(
        DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                pipeline,
                GeneratedStrategySandbox(
                    runtime, systematic.strategy_root / "day-sandbox", GeneratedStrategyLimits()
                ),
                3,
            )
        ),
        build_researcher_context(source, ledger.reader()),
    )


@dataclass(frozen=True, slots=True)
class _ConfiguredDayDiscoveryAction:
    config: ResearchAgentServiceConfig

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        return _day_discovery_executor(self.config, context.observed_at).execute(context)


def _decision_client(config: ResearchAgentServiceConfig) -> ResearchAgentDecisionClient:
    if config.provider_id == "claude-code":
        return ClaudeCliResearchAgentDecisionClient(config.hermes_executable, config.model_id)
    return HermesCliResearchAgentDecisionClient(
        config.hermes_executable,
        config.model_id,
        config.provider_id,
    )


def write_service_report(
    config: ResearchAgentServiceConfig,
    report: ResearchAgentServiceReport | ResearchAgentServiceCycleReport,
) -> None:
    write_private_stable_report(
        config.output_root / "research-agent-runtime-status.json",
        report.model_dump_json() + "\n",
    )
    write_persisted_research_agent_service_health(
        config.output_root,
        health_for_service_report(
            report.config_sha256,
            report.observed_at,
            report.status == "failed",
        ),
    )


def _project_results(config: ResearchAgentServiceConfig, runtime: ResearchAgentRuntime) -> int:
    projected_result_ids = frozenset(
        event.source_event_id for event in HermesDeliveryReader(config.hermes_database).events()
    )
    with HermesDeliveryStore(config.hermes_database).writer() as writer:
        result = project_research_agent_results(
            runtime.store.results(),
            writer,
            evidence=runtime.store.all_evidence(),
            projected_result_ids=projected_result_ids,
        )
    return result.inserted


def _report(
    config_sha256: str,
    operation: Literal["tick", "run"],
    tick: ResearchAgentTickResult,
    projected: int,
    systematic: SystematicInputReportBinding,
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...],
    next_wake_kind: ResearchAgentWakeKind | None,
    next_wake_at: dt.datetime | None,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    return ResearchAgentServiceReport(
        config_sha256=config_sha256,
        operation=operation,
        status=tick.status,
        agent_family_id=tick.agent_family_id,
        cycle_id=tick.cycle_id,
        result_status=None if tick.status == "idle" else tick.status,
        model_calls=tick.model_calls,
        recovered_cycles=tick.recovered_cycles,
        projected_results=projected,
        systematic_input_status=systematic.status,
        systematic_input_sha256=systematic.input_sha256,
        systematic_foundation_sha256=systematic.foundation_sha256,
        family_runtime=family_runtime,
        next_wake_kind=next_wake_kind,
        next_wake_at=next_wake_at,
        observed_at=now,
    )


def _cycle_report(
    config_sha256: str,
    cycle: ResearchAgentBoundedCycleResult,
    projected: int,
    systematic: SystematicInputReportBinding,
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...],
    next_wake_kind: ResearchAgentWakeKind | None,
    next_wake_at: dt.datetime | None,
    now: dt.datetime,
) -> ResearchAgentServiceCycleReport:
    return ResearchAgentServiceCycleReport(
        config_sha256=config_sha256,
        status=cycle.status,
        outcomes=cycle.outcomes,
        family_count=len(cycle.outcomes),
        model_calls=cycle.model_calls,
        recovered_cycles=cycle.recovered_cycles,
        projected_results=projected,
        systematic_input_status=systematic.status,
        systematic_input_sha256=systematic.input_sha256,
        systematic_foundation_sha256=systematic.foundation_sha256,
        family_runtime=family_runtime,
        next_wake_kind=next_wake_kind,
        next_wake_at=next_wake_at,
        observed_at=now,
    )


def _runtime_report_from_store(
    store: ResearchAgentCycleStore,
) -> tuple[
    tuple[ResearchAgentFamilyRuntimeReport, ...],
    ResearchAgentWakeKind | None,
    dt.datetime | None,
]:
    cycles = store.latest_cycles()
    results = store.results()
    cursors: dict[AgentFamilyId, int] = {family: store.cursor(family) for family in PRIMARY_AGENT_FAMILIES}
    return _runtime_report(cycles, results, cursors)


def _runtime_report_from_database(
    path: os.PathLike[str],
) -> tuple[
    tuple[ResearchAgentFamilyRuntimeReport, ...],
    ResearchAgentWakeKind | None,
    dt.datetime | None,
]:
    with sqlite3.connect(f"file:{os.fspath(path)}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only=ON")
        cycles = latest_cycles_from_rows(
            connection.execute(
                "SELECT agent_family_id,payload_json FROM cycles ORDER BY evidence_sequence DESC"
            ).fetchall()
        )
        results = tuple(
            result_from_payload(row[0])
            for row in connection.execute("SELECT payload_json FROM results ORDER BY rowid").fetchall()
        )
        cursors: dict[AgentFamilyId, int] = {
            family: int(row[0])
            if (
                row := connection.execute(
                    "SELECT evidence_sequence FROM cursors WHERE agent_family_id=?",
                    (family,),
                ).fetchone()
            )
            is not None
            else 0
            for family in PRIMARY_AGENT_FAMILIES
        }
    return _runtime_report(cycles, results, cursors)


def _runtime_report(
    cycles: tuple[ResearchAgentCycleV1, ...],
    results: tuple[ResearchAgentResultV1, ...],
    cursors: dict[AgentFamilyId, int],
) -> tuple[
    tuple[ResearchAgentFamilyRuntimeReport, ...],
    ResearchAgentWakeKind | None,
    dt.datetime | None,
]:
    cycle_by_family = {cycle.agent_family_id: cycle for cycle in cycles}
    result_by_cycle = {result.cycle_id: result for result in results}
    family_rows: list[ResearchAgentFamilyRuntimeReport] = []
    for family in PRIMARY_AGENT_FAMILIES:
        cycle = cycle_by_family.get(family)
        result = None if cycle is None else result_by_cycle.get(cycle.cycle_id)
        family_rows.append(
            ResearchAgentFamilyRuntimeReport(
                agent_family_id=family,
                cursor=cursors[family],
                cycle_id=None if cycle is None else str(cycle.cycle_id),
                cycle_state=None if cycle is None else cycle.state,
                result_status=None if result is None else result.status,
                next_wake_kind=None if result is None else result.next_wake_kind,
                next_wake_at=None if result is None else result.next_wake_at,
            )
        )
    family_runtime = tuple(family_rows)
    current_result_rows: list[ResearchAgentResultV1] = []
    for cycle in cycles:
        result = result_by_cycle.get(cycle.cycle_id)
        if result is not None:
            current_result_rows.append(result)
    current_results = tuple(current_result_rows)
    latest = max(current_results, key=lambda result: result.occurred_at, default=None)
    scheduled = tuple(result.next_wake_at for result in current_results if result.next_wake_at is not None)
    aggregate_wake_kind = (
        ResearchAgentWakeKind.SCHEDULED if scheduled else None if latest is None else latest.next_wake_kind
    )
    return (
        family_runtime,
        aggregate_wake_kind,
        min(scheduled, default=None),
    )


def _systematic_input_report(config: ResearchAgentServiceConfig) -> SystematicInputReportBinding:
    activation = load_systematic_input_activation(config.systematic.input_activation)
    match activation:
        case BlockedSystematicInputActivation():
            return SystematicInputReportBinding(
                status="blocked",
                input_sha256=None,
                foundation_sha256=None,
            )
        case ReadySystematicInputActivation() as ready:
            return SystematicInputReportBinding(
                status="ready",
                input_sha256=ready.input_sha256,
                foundation_sha256=ready.foundation_sha256,
            )
        case unreachable:
            assert_never(unreachable)


def _prepare_private_runtime_paths(config: ResearchAgentServiceConfig) -> None:
    for directory in (
        config.output_root,
        config.cycle_database.parent,
        config.hermes_database.parent,
        config.systematic.runs_root,
    ):
        descriptor = open_private_parent(directory, create=True)
        try:
            require_private_directory(descriptor)
        finally:
            os.close(descriptor)


__all__ = (
    "InvalidResearchAgentServiceRuntimeError",
    "ResearchAgentFamilyRuntimeReport",
    "ResearchAgentServiceCycleReport",
    "ResearchAgentServiceReport",
    "build_service_runtime",
    "run_service_cycle",
    "run_service_forever",
    "run_service_tick",
    "service_status",
    "write_service_report",
)
