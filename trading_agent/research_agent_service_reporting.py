from __future__ import annotations

import datetime as dt
import os
import sqlite3
from typing import assert_never

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.research_agent_cycle_models import (
    CycleId,
    ResearchAgentCycleV1,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_store_codec import latest_cycles_from_rows, result_from_payload
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_service_models import (
    DayDiscoveryMarketRuntimeReport,
    ResearchAgentFamilyRuntimeReport,
    SystematicInputReportBinding,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import load_systematic_input_activation
from trading_agent.research_identity_models import MarketId


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


def runtime_report_from_store(
    store: ResearchAgentCycleStore,
) -> tuple[tuple[ResearchAgentFamilyRuntimeReport, ...], ResearchAgentWakeKind | None, dt.datetime | None]:
    cursors: dict[AgentFamilyId, int] = {
        family: store.cursor(family) for family in PRIMARY_AGENT_FAMILIES
    }
    return _runtime_report(store.latest_cycles(), store.results(), cursors)


def runtime_report_from_database(
    path: os.PathLike[str],
) -> tuple[tuple[ResearchAgentFamilyRuntimeReport, ...], ResearchAgentWakeKind | None, dt.datetime | None]:
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
            family: _database_cursor(connection, family) for family in PRIMARY_AGENT_FAMILIES
        }
    return _runtime_report(cycles, results, cursors)


def _database_cursor(connection: sqlite3.Connection, family: AgentFamilyId) -> int:
    if family == "day_trading":
        row = connection.execute(
            "SELECT MAX(evidence_sequence) FROM day_cursors WHERE agent_family_id=?",
            (family,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT evidence_sequence FROM cursors WHERE agent_family_id=?",
            (family,),
        ).fetchone()
    return 0 if row is None or row[0] is None else int(row[0])


def _runtime_report(
    cycles: tuple[ResearchAgentCycleV1, ...],
    results: tuple[ResearchAgentResultV1, ...],
    cursors: dict[AgentFamilyId, int],
) -> tuple[tuple[ResearchAgentFamilyRuntimeReport, ...], ResearchAgentWakeKind | None, dt.datetime | None]:
    cycle_by_family = {cycle.agent_family_id: cycle for cycle in cycles}
    result_by_cycle = {result.cycle_id: result for result in results}
    family_runtime = tuple(
        _family_report(family, cursors[family], cycle_by_family.get(family), result_by_cycle)
        for family in PRIMARY_AGENT_FAMILIES
    )
    current_results = tuple(
        result_by_cycle[cycle.cycle_id] for cycle in cycles if cycle.cycle_id in result_by_cycle
    )
    latest = max(current_results, key=lambda result: result.occurred_at, default=None)
    scheduled = tuple(result.next_wake_at for result in current_results if result.next_wake_at is not None)
    wake_kind = ResearchAgentWakeKind.SCHEDULED if scheduled else None if latest is None else latest.next_wake_kind
    return family_runtime, wake_kind, min(scheduled, default=None)


def _family_report(
    family: AgentFamilyId,
    cursor: int,
    cycle: ResearchAgentCycleV1 | None,
    results: dict[CycleId, ResearchAgentResultV1],
) -> ResearchAgentFamilyRuntimeReport:
    result = None if cycle is None else results.get(cycle.cycle_id)
    return ResearchAgentFamilyRuntimeReport(
        agent_family_id=family,
        cursor=cursor,
        cycle_id=None if cycle is None else str(cycle.cycle_id),
        cycle_state=None if cycle is None else cycle.state,
        result_status=None if result is None else result.status,
        next_wake_kind=None if result is None else result.next_wake_kind,
        next_wake_at=None if result is None else result.next_wake_at,
    )


def systematic_input_report(config: ResearchAgentServiceConfig) -> SystematicInputReportBinding:
    activation = load_systematic_input_activation(config.systematic.input_activation)
    match activation:
        case BlockedSystematicInputActivation():
            return SystematicInputReportBinding("blocked", None, None)
        case ReadySystematicInputActivation() as ready:
            return SystematicInputReportBinding("ready", ready.input_sha256, ready.foundation_sha256)
        case unreachable:
            assert_never(unreachable)


def prepare_private_runtime_paths(config: ResearchAgentServiceConfig) -> None:
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
    "day_discovery_market_runtime",
    "prepare_private_runtime_paths",
    "runtime_report_from_database",
    "runtime_report_from_store",
    "systematic_input_report",
)
