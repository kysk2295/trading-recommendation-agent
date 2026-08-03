from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, assert_never

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativeContract,
    CanonicalDerivativesReason,
)
from trading_agent.dashboard_models_v2 import SourceStateV2
from trading_agent.dashboard_options_workbench_models import (
    OptionChainCellV2,
    OptionChainRowV2,
    OptionChainViewV2,
    OptionsWorkbenchV2,
    WorkbenchSectionV2,
)
from trading_agent.dashboard_options_workbench_promotions import promotion_summaries

_MAX_ROWS = 41


@dataclass(frozen=True, slots=True)
class SourceWorkspaces:
    agent: SourceStateV2 | None
    research: SourceStateV2 | None
    strategies: SourceStateV2 | None


@dataclass(frozen=True, slots=True)
class WorkbenchContext:
    outputs: Path
    now: dt.datetime
    trace_id: str
    workspaces: SourceWorkspaces


@dataclass(frozen=True, slots=True)
class WorkbenchBlock:
    blocker: str
    state: Literal["blocked", "unavailable", "corrupt", "stale"]
    observed_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class ConnectedSectionLabels:
    blocker: str
    unavailable: str
    connected: str


_AGENT_LABELS = ConnectedSectionLabels(
    "derivatives_agent_receipt_missing",
    "파생상품 Researcher 도구 receipt가 아직 연결되지 않았습니다",
    "Exact six-family runtime is observable",
)
_EXPERIMENT_LABELS = ConnectedSectionLabels(
    "options_experiment_missing",
    "옵션 실험 chain이 아직 연결되지 않았습니다",
    "Source-backed experiment and Reviewer ledger is observable",
)


class InvalidOptionsWorkbenchProjectionError(ValueError):
    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__()

    def __str__(self) -> str:
        return self.reason


def delayed_research_workbench(
    contracts: tuple[CanonicalDerivativeContract, ...],
    context: WorkbenchContext,
) -> OptionsWorkbenchV2:
    observed_at = max(contract.quote_observed_at for contract in contracts)
    rows = _rows(contracts, context.trace_id)
    workspaces = context.workspaces
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state="populated",
            observed_at=observed_at,
            blocker_code=None,
            summary="Alpaca indicative options · 15-minute delayed trades and modified quotes · research-only",
            trace_id=context.trace_id,
        ),
        chain=OptionChainViewV2(
            state="populated",
            observed_at=observed_at,
            blocker_code=None,
            summary="Bounded indicative option chain available for research-only strategy composition",
            trace_id=context.trace_id,
            underlying=contracts[0].underlying_symbol,
            selected_expiration=contracts[0].expiration_date.isoformat(),
            expirations=(contracts[0].expiration_date.isoformat(),),
            total_count=len(rows),
            projected_count=len(rows[:_MAX_ROWS]),
            truncated=len(rows) > _MAX_ROWS,
            rows=tuple(rows[:_MAX_ROWS]),
        ),
        scenario=None,
        agent=_connected_section(workspaces.agent, context.trace_id, _AGENT_LABELS),
        experiment=_connected_section(workspaces.research, context.trace_id, _EXPERIMENT_LABELS),
        promotions=promotion_summaries(context.outputs, context.now, workspaces.strategies),
    )


def blocked_from_evidence(
    reason: CanonicalDerivativesReason,
    observed_at: dt.datetime,
    context: WorkbenchContext,
) -> OptionsWorkbenchV2:
    match reason:
        case CanonicalDerivativesReason.OPTIONS_ENTITLEMENT_MISSING:
            block = WorkbenchBlock(reason.value, "unavailable", None)
        case CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID:
            block = WorkbenchBlock(reason.value, "corrupt", observed_at)
        case CanonicalDerivativesReason.DERIVATIVE_SURFACE_STALE:
            block = WorkbenchBlock(reason.value, "stale", observed_at)
        case (
            CanonicalDerivativesReason.CURRENT_QUOTE_NOT_LICENSED
            | CanonicalDerivativesReason.DERIVATIVES_EVIDENCE_OVER_BROAD
            | CanonicalDerivativesReason.CME_SUB_ENTITLEMENT_MISSING
        ):
            block = WorkbenchBlock(reason.value, "blocked", observed_at)
        case CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY:
            raise InvalidOptionsWorkbenchProjectionError(reason="unexpected_indicative_terminal")
        case unreachable:
            assert_never(unreachable)
    return blocked_workbench(block, context)


def blocked_workbench(block: WorkbenchBlock, context: WorkbenchContext) -> OptionsWorkbenchV2:
    summary = f"Option evidence blocked: {block.blocker}"
    workspaces = context.workspaces
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state=block.state,
            observed_at=block.observed_at,
            blocker_code=block.blocker,
            summary=summary,
            trace_id=context.trace_id,
        ),
        chain=OptionChainViewV2(
            state=block.state,
            observed_at=block.observed_at,
            blocker_code=block.blocker,
            summary=summary,
            trace_id=context.trace_id,
            underlying=None,
            selected_expiration=None,
            expirations=(),
            total_count=0,
            projected_count=0,
            truncated=False,
            rows=(),
        ),
        scenario=None,
        agent=_connected_section(workspaces.agent, context.trace_id, _AGENT_LABELS),
        experiment=_connected_section(workspaces.research, context.trace_id, _EXPERIMENT_LABELS),
        promotions=promotion_summaries(context.outputs, context.now, workspaces.strategies),
    )


def _rows(
    contracts: tuple[CanonicalDerivativeContract, ...],
    trace_id: str,
) -> tuple[OptionChainRowV2, ...]:
    cells: dict[Decimal, tuple[OptionChainCellV2 | None, OptionChainCellV2 | None]] = {}
    for contract in sorted(contracts, key=lambda item: (item.strike_price, item.instrument_id)):
        cell = OptionChainCellV2(
            contract_id=contract.instrument_id,
            side=contract.contract_type.value,
            provider="alpaca",
            state="indicative",
            bid=format(contract.bid_price, "f"),
            ask=format(contract.ask_price, "f"),
            observed_at=contract.quote_observed_at,
            trace_id=trace_id,
            selectable=True,
        )
        call, put = cells.get(contract.strike_price, (None, None))
        match contract.contract_type:
            case OptionContractType.CALL:
                cells[contract.strike_price] = (cell, put)
            case OptionContractType.PUT:
                cells[contract.strike_price] = (call, cell)
            case unreachable:
                assert_never(unreachable)
    return tuple(
        OptionChainRowV2(strike=format(strike, "f"), call=call, put=put)
        for strike, (call, put) in sorted(cells.items())
    )


def _connected_section(
    workspace: SourceStateV2 | None,
    fallback_trace_id: str,
    labels: ConnectedSectionLabels,
) -> WorkbenchSectionV2:
    if workspace is None:
        return WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            blocker_code=labels.blocker,
            summary=labels.unavailable,
            trace_id=fallback_trace_id,
        )
    summary = labels.connected
    if workspace.total_count > 0:
        summary = f"{labels.connected} · {workspace.projected_count}/{workspace.total_count}"
    return WorkbenchSectionV2(
        state=workspace.state,
        observed_at=workspace.observed_at,
        blocker_code=workspace.blocker_code,
        summary=summary,
        trace_id=workspace.trace_id,
    )


__all__ = (
    "InvalidOptionsWorkbenchProjectionError",
    "SourceWorkspaces",
    "WorkbenchBlock",
    "WorkbenchContext",
    "blocked_from_evidence",
    "blocked_workbench",
    "delayed_research_workbench",
)
