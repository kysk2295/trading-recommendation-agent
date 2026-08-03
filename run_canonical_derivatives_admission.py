#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_canonical_derivatives_admission.py --help
# ─────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from trading_agent.alpaca_option_chain_models import (
    OptionChainRequest,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore
from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
)
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
)
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativesAdmissionRequest,
    CanonicalDerivativesStatus,
)
from trading_agent.canonical_derivatives_projection import (
    project_canonical_derivatives_evidence,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


def main(
    contract_collection_id: Annotated[str, typer.Option()],
    chain_collection_id: Annotated[str, typer.Option()],
    underlying_symbol: Annotated[str, typer.Option()],
    expiration_date: Annotated[str, typer.Option()],
    contract_type: Annotated[OptionContractType, typer.Option()],
    contract_database: Annotated[Path, typer.Option()],
    chain_database: Annotated[Path, typer.Option()],
    as_of: Annotated[str, typer.Option()],
    feed: Annotated[OptionFeed, typer.Option()] = OptionFeed.INDICATIVE,
    contract_limit: Annotated[int, typer.Option()] = 100,
    chain_limit: Annotated[int, typer.Option()] = 1_000,
    max_pages: Annotated[int, typer.Option()] = 2,
    freshness_seconds: Annotated[int, typer.Option()] = 1_200,
    max_contracts: Annotated[int, typer.Option()] = 1_000,
    kis_admission: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        expiry = dt.date.fromisoformat(expiration_date)
        evidence = project_canonical_derivatives_evidence(
            AlpacaOptionContractStore(contract_database),
            AlpacaOptionChainStore(chain_database),
            CanonicalDerivativesAdmissionRequest(
                contract_request=OptionContractCatalogRequest(
                    collection_id=contract_collection_id,
                    underlying_symbol=underlying_symbol,
                    expiration_date=expiry,
                    contract_type=contract_type,
                    limit=contract_limit,
                    max_pages=max_pages,
                ),
                chain_request=OptionChainRequest(
                    collection_id=chain_collection_id,
                    underlying_symbol=underlying_symbol,
                    feed=feed,
                    expiration_date=expiry,
                    contract_type=contract_type,
                    limit=chain_limit,
                    max_pages=max_pages,
                ),
                as_of=dt.datetime.fromisoformat(as_of),
                freshness_seconds=freshness_seconds,
                max_contracts=max_contracts,
                kis_admission_path=kis_admission,
            ),
        )
    except (OSError, TypeError, ValueError):
        raise typer.BadParameter("canonical derivatives admission input is invalid") from None
    typer.echo(canonical_experiment_ledger_json(evidence))
    if evidence.status is CanonicalDerivativesStatus.BLOCKED:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    typer.run(main)
