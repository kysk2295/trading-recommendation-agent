from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStoreError
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStoreError
from trading_agent.canonical_derivatives_models import CanonicalDerivativesStatus
from trading_agent.dashboard_models_v2 import SourceStateV2
from trading_agent.dashboard_options_workbench_models import OptionsWorkbenchV2
from trading_agent.dashboard_options_workbench_sources import project_latest_delayed_options
from trading_agent.dashboard_options_workbench_views import (
    InvalidOptionsWorkbenchProjectionError,
    SourceWorkspaces,
    WorkbenchBlock,
    WorkbenchContext,
    blocked_from_evidence,
    blocked_workbench,
    delayed_research_workbench,
)


def project_options_workbench(
    *,
    outputs: Path,
    now: dt.datetime,
    derivatives_trace_id: str,
    agent_workspace: SourceStateV2 | None = None,
    research_workspace: SourceStateV2 | None = None,
    strategies_workspace: SourceStateV2 | None = None,
) -> OptionsWorkbenchV2:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidOptionsWorkbenchProjectionError(reason="projection_time_not_aware")
    context = WorkbenchContext(
        outputs=outputs,
        now=now,
        trace_id=derivatives_trace_id,
        workspaces=SourceWorkspaces(agent_workspace, research_workspace, strategies_workspace),
    )
    try:
        evidence = project_latest_delayed_options(outputs / "derivatives", now)
    except (
        AlpacaOptionChainStoreError,
        AlpacaOptionContractStoreError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return blocked_workbench(
            WorkbenchBlock("derivatives_source_invalid", "corrupt", now),
            context,
        )
    if not evidence:
        return blocked_workbench(
            WorkbenchBlock("canonical_option_chain_missing", "unavailable", None),
            context,
        )
    ready_contracts = tuple(
        contract
        for item in evidence
        if item.status is CanonicalDerivativesStatus.READY
        for contract in item.contracts
    )
    if ready_contracts:
        return delayed_research_workbench(ready_contracts, context)
    latest = evidence[0]
    match latest.status:
        case CanonicalDerivativesStatus.BLOCKED:
            return blocked_from_evidence(latest.terminal_reason, latest.observed_at, context)
        case CanonicalDerivativesStatus.READY:
            raise InvalidOptionsWorkbenchProjectionError(reason="ready_evidence_without_contracts")
        case unreachable:
            assert_never(unreachable)


__all__ = ("InvalidOptionsWorkbenchProjectionError", "project_options_workbench")
