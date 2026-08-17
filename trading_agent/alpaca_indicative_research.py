from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trading_agent.alpaca_option_chain_collection import OptionChainPageFetcher, collect_alpaca_option_chain
from trading_agent.alpaca_option_chain_models import (
    OptionChainRequest,
    OptionChainStatus,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore
from trading_agent.alpaca_option_contract_collection import (
    OptionContractPageFetcher,
    collect_alpaca_option_contracts,
)
from trading_agent.alpaca_option_contract_models import (
    OptionCatalogStatus,
    OptionContractCatalogRequest,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class IndicativeResearchCollectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IndicativeResearchPlan:
    session_date: dt.date
    expiration_date: dt.date
    underlying_symbol: str = "SPY"


@dataclass(frozen=True, slots=True)
class IndicativeResearchCollection:
    session_date: dt.date
    expiration_date: dt.date
    chain_snapshots: int
    contracts: int
    replayed: bool
    network_sources: int


class IndicativeResearchPaths(Protocol):
    outputs_root: Path


def plan_indicative_research(now: dt.datetime) -> IndicativeResearchPlan | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise IndicativeResearchCollectionError
    current = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None or current < bounds[0] + dt.timedelta(minutes=15):
        return None
    return IndicativeResearchPlan(current.date(), _next_regular_friday(current.date()))


def indicative_research_requests(
    plan: IndicativeResearchPlan,
) -> tuple[tuple[OptionContractCatalogRequest, OptionChainRequest], ...]:
    return tuple(
        _request_pair(plan, contract_type)
        for contract_type in (OptionContractType.CALL, OptionContractType.PUT)
    )


def indicative_research_requires_network(plan: IndicativeResearchPlan, outputs: Path) -> bool:
    root = outputs / "derivatives"
    catalog_store = AlpacaOptionContractStore(root / "option-contracts.sqlite3")
    chain_store = AlpacaOptionChainStore(root / "option-chain.sqlite3")
    return any(
        catalog_store.run(catalog_request.request_id) is None
        or chain_store.run(chain_request.request_id) is None
        for catalog_request, chain_request in indicative_research_requests(plan)
    )


def collect_indicative_research(
    plan: IndicativeResearchPlan,
    outputs: Path,
    catalog_fetcher: OptionContractPageFetcher | None,
    chain_fetcher: OptionChainPageFetcher | None,
) -> IndicativeResearchCollection:
    root = outputs / "derivatives"
    catalog_store = AlpacaOptionContractStore(root / "option-contracts.sqlite3")
    chain_store = AlpacaOptionChainStore(root / "option-chain.sqlite3")
    chain_snapshots = 0
    contracts = 0
    network_sources = 0
    for catalog_request, chain_request in indicative_research_requests(plan):
        catalog = catalog_store.run(catalog_request.request_id)
        chain = chain_store.run(chain_request.request_id)
        if catalog is None:
            if catalog_fetcher is None:
                raise IndicativeResearchCollectionError
            catalog_store.preflight_write()
            catalog = collect_alpaca_option_contracts(catalog_fetcher, catalog_store, catalog_request).run
            network_sources += 1
        if catalog.status is not OptionCatalogStatus.SUCCESS:
            raise IndicativeResearchCollectionError
        if chain is None:
            if chain_fetcher is None:
                raise IndicativeResearchCollectionError
            chain_store.preflight_write()
            chain = collect_alpaca_option_chain(chain_fetcher, chain_store, chain_request).run
            network_sources += 1
        if chain.status is not OptionChainStatus.SUCCESS or not chain.snapshots:
            raise IndicativeResearchCollectionError
        contracts += len(catalog.contracts)
        chain_snapshots += len(chain.snapshots)
    return IndicativeResearchCollection(
        session_date=plan.session_date,
        expiration_date=plan.expiration_date,
        chain_snapshots=chain_snapshots,
        contracts=contracts,
        replayed=network_sources == 0,
        network_sources=network_sources,
    )


def _request_pair(
    plan: IndicativeResearchPlan,
    contract_type: OptionContractType,
) -> tuple[OptionContractCatalogRequest, OptionChainRequest]:
    identity = f"{plan.session_date.isoformat()}-{plan.underlying_symbol.lower()}-{contract_type.value}"
    return (
        OptionContractCatalogRequest(
            collection_id=f"indicative-catalog-{identity}",
            underlying_symbol=plan.underlying_symbol,
            expiration_date=plan.expiration_date,
            contract_type=contract_type,
            limit=100,
            max_pages=3,
        ),
        OptionChainRequest(
            collection_id=f"indicative-chain-{identity}",
            underlying_symbol=plan.underlying_symbol,
            feed=OptionFeed.INDICATIVE,
            expiration_date=plan.expiration_date,
            contract_type=contract_type,
            limit=1_000,
            max_pages=2,
        ),
    )


def _next_regular_friday(after: dt.date) -> dt.date:
    candidate = after + dt.timedelta(days=(4 - after.weekday()) % 7 or 7)
    while regular_session_bounds(candidate) is None:
        candidate += dt.timedelta(days=7)
    return candidate


__all__ = (
    "IndicativeResearchCollection",
    "IndicativeResearchCollectionError",
    "IndicativeResearchPaths",
    "IndicativeResearchPlan",
    "collect_indicative_research",
    "indicative_research_requests",
    "indicative_research_requires_network",
    "plan_indicative_research",
)
