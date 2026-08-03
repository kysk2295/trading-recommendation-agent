from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Final, Literal, assert_never

from trading_agent.alpaca_option_chain_capability import (
    AlpacaOptionChainCapabilityError,
    project_alpaca_option_chain_capability,
)
from trading_agent.alpaca_option_chain_models import OptionChainStatus, OptionFeed
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.alpaca_option_contract_models import OptionCatalogStatus
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.dashboard_derivatives_section import DerivativesSection
from trading_agent.dashboard_models_v2 import (
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.data_capability_models import RedistributionPolicy

_REQUEST_ID_QUERIES: Final = {
    "alpaca_option_chain_runs": ("SELECT request_id FROM alpaca_option_chain_runs ORDER BY rowid DESC LIMIT 1"),
    "alpaca_option_contract_runs": ("SELECT request_id FROM alpaca_option_contract_runs ORDER BY rowid DESC LIMIT 1"),
}
OPTIONS_TRACE_ID: Final = "trace.derivatives.options"


def read_options_section(outputs: Path, now: dt.datetime) -> DerivativesSection:
    chain_path = outputs / "derivatives" / "option-chain.sqlite3"
    contract_path = outputs / "derivatives" / "option-contracts.sqlite3"
    chain_id = _latest_id(chain_path, "alpaca_option_chain_runs")
    contract_id = _latest_id(contract_path, "alpaca_option_contract_runs")
    if chain_id is None or contract_id is None:
        return _missing(now, "options_entitlement_missing")
    try:
        chain = AlpacaOptionChainStore(chain_path).run(chain_id)
        catalog = AlpacaOptionContractStore(contract_path).run(contract_id)
        if chain is None or catalog is None:
            return _missing(now, "options_entitlement_missing")
        capability = project_alpaca_option_chain_capability(chain)
    except (
        AlpacaOptionChainCapabilityError,
        AlpacaOptionChainStoreError,
        AlpacaOptionContractStoreError,
    ):
        return _missing(now, "options_receipt_invalid", corrupt=True)
    if chain.completed_at > now + dt.timedelta(minutes=5) or catalog.completed_at > now + dt.timedelta(minutes=5):
        return _missing(now, "derivative_future_observation", corrupt=True)
    match (chain.status, catalog.status):
        case (OptionChainStatus.SUCCESS, OptionCatalogStatus.SUCCESS):
            pass
        case (OptionChainStatus.FAILED, _) | (_, OptionCatalogStatus.FAILED):
            return _missing(now, "options_collection_failed")
        case unreachable:
            assert_never(unreachable)
    current = now - chain.completed_at <= dt.timedelta(minutes=20)
    licensed = (
        capability.entitlement.real_time
        and capability.entitlement.redistribution_policy is not RedistributionPolicy.NONE
    )
    match chain.request.feed:
        case OptionFeed.INDICATIVE:
            blocker = "indicative_research_only"
        case OptionFeed.OPRA:
            blocker = (
                None
                if current and licensed
                else ("current_quote_not_licensed" if not licensed else "options_receipt_stale")
            )
        case unreachable:
            assert_never(unreachable)
    source_id = OPTIONS_TRACE_ID
    items = tuple(
        WorkspaceItemV2(
            item_id=f"derivative.option.{index}",
            kind="derivative",
            label=contract.root_symbol,
            state="populated" if blocker is None else "blocked",
            value=f"{contract.expiration_date.isoformat()}:{contract.contract_type.value}",
            observed_at=contract.observed_at,
            trace_id=source_id,
        )
        for index, contract in enumerate(catalog.contracts[:24])
    )
    source = TraceNodeV2(
        node_id=source_id,
        kind="source_receipt",
        label="Typed option chain and contract stores",
        observed_at=max(chain.completed_at, catalog.completed_at),
        safe_ref=chain.run_id,
        state="accepted",
        source_namespace="derivatives.options",
    )
    if blocker is None:
        return DerivativesSection(
            "populated" if items else "empty",
            None,
            chain.completed_at,
            items,
            (source,),
            (),
        )
    terminal = TraceNodeV2(
        node_id=f"{source_id}.blocker",
        kind="blocker_terminal",
        label="Option entitlement gate",
        observed_at=chain.completed_at,
        safe_ref=catalog.run_id,
        state="blocked",
        source_namespace="derivatives.options",
    )
    return DerivativesSection(
        "blocked",
        blocker,
        chain.completed_at,
        items,
        (source, terminal),
        (TraceEdgeV2(from_node_id=source_id, to_node_id=terminal.node_id, kind="blocked_by"),),
    )


def _latest_id(
    path: Path,
    table: Literal["alpaca_option_chain_runs", "alpaca_option_contract_runs"],
) -> str | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row: tuple[str] | None = connection.execute(_REQUEST_ID_QUERIES[table]).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


def _missing(
    now: dt.datetime,
    blocker: str,
    *,
    corrupt: bool = False,
) -> DerivativesSection:
    source_id = OPTIONS_TRACE_ID
    safe_ref = "0" * 64
    nodes = (
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Typed option authority",
            observed_at=now,
            safe_ref=safe_ref,
            state="unavailable",
            source_namespace="derivatives.options",
        ),
        TraceNodeV2(
            node_id=f"{source_id}.blocker",
            kind="blocker_terminal",
            label="Option authority blocker",
            observed_at=now,
            safe_ref=safe_ref,
            state="blocked",
            source_namespace="derivatives.options",
        ),
    )
    return DerivativesSection(
        "corrupt" if corrupt else "unavailable",
        blocker,
        now if corrupt else None,
        (),
        nodes,
        (TraceEdgeV2(from_node_id=source_id, to_node_id=f"{source_id}.blocker", kind="blocked_by"),),
    )


__all__ = ("OPTIONS_TRACE_ID", "read_options_section")
