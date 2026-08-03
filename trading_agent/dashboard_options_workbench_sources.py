from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Final

from trading_agent.alpaca_option_chain_models import OptionChainRun, OptionContractType
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore
from trading_agent.alpaca_option_contract_models import OptionContractCatalogRun
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativesAdmissionRequest,
    CanonicalDerivativesEvidence,
)
from trading_agent.canonical_derivatives_projection import project_canonical_derivatives_evidence

_MAX_RUN_CANDIDATES: Final = 32
_LATEST_CHAIN_IDS: Final = (
    "SELECT request_id FROM alpaca_option_chain_runs ORDER BY rowid DESC LIMIT 32"
)
_LATEST_CATALOG_IDS: Final = (
    "SELECT request_id FROM alpaca_option_contract_runs ORDER BY rowid DESC LIMIT 32"
)


def project_latest_delayed_options(
    root: Path,
    now: dt.datetime,
) -> tuple[CanonicalDerivativesEvidence, ...]:
    chain_path = root / "option-chain.sqlite3"
    catalog_path = root / "option-contracts.sqlite3"
    if not chain_path.is_file() or not catalog_path.is_file():
        return ()
    chain_store = AlpacaOptionChainStore(chain_path)
    catalog_store = AlpacaOptionContractStore(catalog_path)
    chain_runs = _chain_runs(chain_store, _latest_ids(chain_path, _LATEST_CHAIN_IDS))
    catalog_runs = _catalog_runs(catalog_store, _latest_ids(catalog_path, _LATEST_CATALOG_IDS))
    if not chain_runs or not catalog_runs:
        return ()
    scope = chain_runs[0].request
    selected_chains = {
        contract_type: next(
            (
                run
                for run in chain_runs
                if run.request.underlying_symbol == scope.underlying_symbol
                and run.request.expiration_date == scope.expiration_date
                and run.request.contract_type is contract_type
            ),
            None,
        )
        for contract_type in OptionContractType
    }
    selected_catalogs = {
        contract_type: next(
            (
                run
                for run in catalog_runs
                if run.request.underlying_symbol == scope.underlying_symbol
                and run.request.expiration_date == scope.expiration_date
                and run.request.contract_type is contract_type
            ),
            None,
        )
        for contract_type in OptionContractType
    }
    evidence: list[CanonicalDerivativesEvidence] = []
    for contract_type in OptionContractType:
        chain = selected_chains[contract_type]
        catalog = selected_catalogs[contract_type]
        if chain is None or catalog is None:
            continue
        evidence.append(
            project_canonical_derivatives_evidence(
                catalog_store,
                chain_store,
                CanonicalDerivativesAdmissionRequest(
                    contract_request=catalog.request,
                    chain_request=chain.request,
                    as_of=now,
                    freshness_seconds=86_400,
                ),
            )
        )
    return tuple(sorted(evidence, key=lambda item: item.observed_at, reverse=True))


def _latest_ids(path: Path, query: str) -> tuple[str, ...]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        rows: list[tuple[str]] = connection.execute(query).fetchall()
    return tuple(row[0] for row in rows[:_MAX_RUN_CANDIDATES])


def _chain_runs(
    store: AlpacaOptionChainStore,
    request_ids: tuple[str, ...],
) -> tuple[OptionChainRun, ...]:
    runs = tuple(store.run(request_id) for request_id in request_ids)
    return tuple(run for run in runs if run is not None)


def _catalog_runs(
    store: AlpacaOptionContractStore,
    request_ids: tuple[str, ...],
) -> tuple[OptionContractCatalogRun, ...]:
    runs = tuple(store.run(request_id) for request_id in request_ids)
    return tuple(run for run in runs if run is not None)


__all__ = ("project_latest_delayed_options",)
