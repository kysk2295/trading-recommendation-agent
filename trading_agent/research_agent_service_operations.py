from __future__ import annotations

import datetime as dt
from typing import Literal

import anyio

from trading_agent.dashboard_agent_cycle_runtime import read_cycle_runtime_observations
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_cycle_models import ResearchAgentWakeKind
from trading_agent.research_agent_runtime import (
    ResearchAgentBoundedCycleResult,
    ResearchAgentTickResult,
)
from trading_agent.research_agent_runtime_lease import research_agent_runtime_lease
from trading_agent.research_agent_service_builder import build_service_runtime
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
)
from trading_agent.research_agent_service_health import (
    health_for_service_report,
    write_persisted_research_agent_service_health,
)
from trading_agent.research_agent_service_models import (
    InvalidResearchAgentServiceRuntimeError,
    ResearchAgentFamilyRuntimeReport,
    ResearchAgentServiceCycleReport,
    ResearchAgentServiceReport,
    SystematicInputReportBinding,
)
from trading_agent.research_agent_service_projection import project_service_results as _project_results
from trading_agent.research_agent_service_reporting import (
    prepare_private_runtime_paths,
    runtime_report_from_database,
    runtime_report_from_store,
    systematic_input_report,
)


def run_service_tick(config: ResearchAgentServiceConfig, now: dt.datetime) -> ResearchAgentServiceReport:
    systematic = systematic_input_report(config)
    runtime = build_service_runtime(config)
    try:
        tick = runtime.tick(now)
        projected = _project_results(config, runtime, now)
        family_runtime, wake_kind, wake_at = runtime_report_from_store(runtime.store)
        report = _report(config, "tick", tick, projected, systematic, family_runtime, wake_kind, wake_at, now)
        write_service_report(config, report)
        return report
    finally:
        runtime.close()


def run_service_cycle(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceCycleReport:
    systematic = systematic_input_report(config)
    runtime = build_service_runtime(config)
    try:
        cycle = runtime.cycle(now)
        projected = _project_results(config, runtime, now)
        family_runtime, wake_kind, wake_at = runtime_report_from_store(runtime.store)
        report = _cycle_report(config, cycle, projected, systematic, family_runtime, wake_kind, wake_at, now)
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
    prepare_private_runtime_paths(config)
    with research_agent_runtime_lease(config.output_root / "research-agent-runtime.lock"):
        runtime = build_service_runtime(config)
        try:
            while True:
                now = dt.datetime.now(dt.UTC)
                systematic = systematic_input_report(config)
                tick = runtime.tick(now)
                projected = _project_results(config, runtime, now)
                family_runtime, wake_kind, wake_at = runtime_report_from_store(runtime.store)
                write_service_report(
                    config,
                    _report(config, "run", tick, projected, systematic, family_runtime, wake_kind, wake_at, now),
                )
                await anyio.sleep(tick_seconds)
        finally:
            runtime.close()


def service_status(config: ResearchAgentServiceConfig, now: dt.datetime) -> ResearchAgentServiceReport:
    systematic = systematic_input_report(config)
    if not config.cycle_database.exists():
        family_runtime = tuple(
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
        )
        return _status_report(config, now, systematic, family_runtime)
    observations = read_cycle_runtime_observations(config.cycle_database)
    family_runtime, wake_kind, wake_at = runtime_report_from_database(config.cycle_database)
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
        next_wake_kind=wake_kind,
        next_wake_at=wake_at,
        observed_at=now,
    )


def _status_report(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
    systematic: SystematicInputReportBinding,
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...],
) -> ResearchAgentServiceReport:
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
        family_runtime=family_runtime,
        next_wake_kind=None,
        next_wake_at=None,
        observed_at=now,
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
        health_for_service_report(report.config_sha256, report.observed_at, report.status == "failed"),
    )


def _report(
    config: ResearchAgentServiceConfig,
    operation: Literal["tick", "run"],
    tick: ResearchAgentTickResult,
    projected: int,
    systematic: SystematicInputReportBinding,
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...],
    wake_kind: ResearchAgentWakeKind | None,
    wake_at: dt.datetime | None,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    return ResearchAgentServiceReport(
        config_sha256=canonical_research_agent_service_config_sha256(config),
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
        next_wake_kind=wake_kind,
        next_wake_at=wake_at,
        observed_at=now,
    )


def _cycle_report(
    config: ResearchAgentServiceConfig,
    cycle: ResearchAgentBoundedCycleResult,
    projected: int,
    systematic: SystematicInputReportBinding,
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...],
    wake_kind: ResearchAgentWakeKind | None,
    wake_at: dt.datetime | None,
    now: dt.datetime,
) -> ResearchAgentServiceCycleReport:
    return ResearchAgentServiceCycleReport(
        config_sha256=canonical_research_agent_service_config_sha256(config),
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
        next_wake_kind=wake_kind,
        next_wake_at=wake_at,
        observed_at=now,
    )


__all__ = (
    "run_service_cycle",
    "run_service_forever",
    "run_service_tick",
    "service_status",
    "write_service_report",
)
