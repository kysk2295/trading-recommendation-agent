from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, assert_never, override

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativeContract,
    CanonicalDerivativesAdmissionRequest,
    CanonicalDerivativesReason,
    CanonicalDerivativesStatus,
)
from trading_agent.canonical_derivatives_projection import (
    project_canonical_derivatives_evidence,
)
from trading_agent.dashboard_options_workbench_models import (
    OptionChainCellV2,
    OptionChainRowV2,
    OptionChainViewV2,
    OptionsWorkbenchV2,
    WorkbenchSectionV2,
)

_MAX_ROWS: Final = 41
_REQUEST_ID_QUERIES: Final[dict[Literal["chain", "catalog"], str]] = {
    "chain": "SELECT request_id FROM alpaca_option_chain_runs ORDER BY rowid DESC LIMIT 1",
    "catalog": "SELECT request_id FROM alpaca_option_contract_runs ORDER BY rowid DESC LIMIT 1",
}


@dataclass(frozen=True, slots=True)
class InvalidOptionsWorkbenchProjectionError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def project_options_workbench(*, outputs: Path, now: dt.datetime, derivatives_trace_id: str) -> OptionsWorkbenchV2:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidOptionsWorkbenchProjectionError(reason="projection_time_not_aware")
    root = outputs / "derivatives"
    try:
        chain_id = _latest_id(root / "option-chain.sqlite3", "chain")
        catalog_id = _latest_id(root / "option-contracts.sqlite3", "catalog")
        if chain_id is None or catalog_id is None:
            return _blocked_workbench(derivatives_trace_id, "canonical_option_chain_missing", "unavailable", None)
        chain_store = AlpacaOptionChainStore(root / "option-chain.sqlite3")
        catalog_store = AlpacaOptionContractStore(root / "option-contracts.sqlite3")
        chain = chain_store.run(chain_id)
        catalog = catalog_store.run(catalog_id)
        if chain is None or catalog is None:
            return _blocked_workbench(derivatives_trace_id, "canonical_option_chain_missing", "unavailable", None)
        evidence = project_canonical_derivatives_evidence(
            catalog_store,
            chain_store,
            CanonicalDerivativesAdmissionRequest(
                contract_request=catalog.request,
                chain_request=chain.request,
                as_of=now,
            ),
        )
    except (AlpacaOptionChainStoreError, AlpacaOptionContractStoreError, sqlite3.Error, ValueError):
        return _blocked_workbench(derivatives_trace_id, "derivatives_source_invalid", "corrupt", now)
    match evidence.status:
        case CanonicalDerivativesStatus.READY:
            return _research_only_workbench(evidence.contracts, derivatives_trace_id)
        case CanonicalDerivativesStatus.BLOCKED:
            return _blocked_from_evidence(derivatives_trace_id, evidence.terminal_reason, evidence.observed_at)
        case unreachable:
            assert_never(unreachable)


def _research_only_workbench(contracts: tuple[CanonicalDerivativeContract, ...], trace_id: str) -> OptionsWorkbenchV2:
    observed_at = max(contract.quote_observed_at for contract in contracts)
    rows = _rows(contracts, trace_id)
    blocker = "indicative_research_only"
    summary = "Indicative Alpaca option research evidence; authority unavailable"
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state="blocked",
            observed_at=observed_at,
            blocker_code=blocker,
            summary=summary,
            trace_id=trace_id,
        ),
        chain=OptionChainViewV2(
            state="blocked",
            observed_at=observed_at,
            blocker_code=blocker,
            summary="Indicative option chain is research-only and not selectable",
            trace_id=trace_id,
            underlying=contracts[0].underlying_symbol,
            selected_expiration=contracts[0].expiration_date.isoformat(),
            expirations=(contracts[0].expiration_date.isoformat(),),
            total_count=len(rows),
            projected_count=len(rows[:_MAX_ROWS]),
            truncated=len(rows) > _MAX_ROWS,
            rows=tuple(rows[:_MAX_ROWS]),
        ),
        scenario=None,
        agent=_unavailable(
            trace_id, "derivatives_agent_receipt_missing", "파생상품 Researcher 도구 receipt가 아직 연결되지 않았습니다"
        ),
        experiment=_unavailable(trace_id, "options_experiment_missing", "옵션 실험 chain이 아직 연결되지 않았습니다"),
        promotions=(),
    )


def _rows(contracts: tuple[CanonicalDerivativeContract, ...], trace_id: str) -> tuple[OptionChainRowV2, ...]:
    rows: list[OptionChainRowV2] = []
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
            selectable=False,
        )
        match contract.contract_type:
            case OptionContractType.CALL:
                rows.append(OptionChainRowV2(strike=format(contract.strike_price, "f"), call=cell, put=None))
            case OptionContractType.PUT:
                rows.append(OptionChainRowV2(strike=format(contract.strike_price, "f"), call=None, put=cell))
            case unreachable:
                assert_never(unreachable)
    return tuple(rows)


def _blocked_from_evidence(
    trace_id: str, reason: CanonicalDerivativesReason, observed_at: dt.datetime
) -> OptionsWorkbenchV2:
    match reason:
        case CanonicalDerivativesReason.OPTIONS_ENTITLEMENT_MISSING:
            state, observation = "unavailable", None
        case CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID:
            state, observation = "corrupt", observed_at
        case CanonicalDerivativesReason.DERIVATIVE_SURFACE_STALE:
            state, observation = "stale", observed_at
        case (
            CanonicalDerivativesReason.CURRENT_QUOTE_NOT_LICENSED
            | CanonicalDerivativesReason.DERIVATIVES_EVIDENCE_OVER_BROAD
            | CanonicalDerivativesReason.CME_SUB_ENTITLEMENT_MISSING
        ):
            state, observation = "blocked", observed_at
        case CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY:
            raise InvalidOptionsWorkbenchProjectionError(reason="unexpected_indicative_terminal")
        case unreachable:
            assert_never(unreachable)
    return _blocked_workbench(trace_id, reason.value, state, observation)


def _blocked_workbench(
    trace_id: str,
    blocker: str,
    state: Literal["blocked", "unavailable", "corrupt", "stale"],
    observed_at: dt.datetime | None,
) -> OptionsWorkbenchV2:
    summary = f"Option evidence blocked: {blocker}"
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state=state,
            observed_at=observed_at,
            blocker_code=blocker,
            summary=summary,
            trace_id=trace_id,
        ),
        chain=OptionChainViewV2(
            state=state,
            observed_at=observed_at,
            blocker_code=blocker,
            summary=summary,
            trace_id=trace_id,
            underlying=None,
            selected_expiration=None,
            expirations=(),
            total_count=0,
            projected_count=0,
            truncated=False,
            rows=(),
        ),
        scenario=None,
        agent=_unavailable(
            trace_id, "derivatives_agent_receipt_missing", "파생상품 Researcher 도구 receipt가 아직 연결되지 않았습니다"
        ),
        experiment=_unavailable(trace_id, "options_experiment_missing", "옵션 실험 chain이 아직 연결되지 않았습니다"),
        promotions=(),
    )


def _latest_id(path: Path, source: Literal["chain", "catalog"]) -> str | None:
    if not path.is_file():
        return None
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        row: tuple[str] | None = connection.execute(_REQUEST_ID_QUERIES[source]).fetchone()
    return None if row is None else row[0]


def _unavailable(trace_id: str, blocker: str, summary: str) -> WorkbenchSectionV2:
    return WorkbenchSectionV2(
        state="unavailable",
        observed_at=None,
        blocker_code=blocker,
        summary=summary,
        trace_id=trace_id,
    )


__all__ = ("InvalidOptionsWorkbenchProjectionError", "project_options_workbench")
