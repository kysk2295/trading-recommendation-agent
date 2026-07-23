#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_surface.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_option_chain_models import (
    OptionChainRequest,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
)
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.alpaca_option_surface import (
    AlpacaOptionSurface,
    AlpacaOptionSurfaceError,
    OptionSurfaceStatus,
    build_alpaca_option_surface,
    publish_alpaca_option_surface,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_surface_ko.md"


def main(
    contract_collection_id: Annotated[str, typer.Option()],
    chain_collection_id: Annotated[str, typer.Option()],
    underlying_symbol: Annotated[str, typer.Option()],
    expiration_date: Annotated[str, typer.Option()],
    contract_type: Annotated[OptionContractType, typer.Option()],
    feed: Annotated[OptionFeed, typer.Option()],
    contract_database: Annotated[Path, typer.Option()],
    chain_database: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    contract_limit: Annotated[int, typer.Option()] = 100,
    chain_limit: Annotated[int, typer.Option()] = 1_000,
    max_pages: Annotated[int, typer.Option()] = 2,
) -> None:
    try:
        expiry = dt.date.fromisoformat(expiration_date)
        contract_request = OptionContractCatalogRequest(
            collection_id=contract_collection_id,
            underlying_symbol=underlying_symbol,
            expiration_date=expiry,
            contract_type=contract_type,
            limit=contract_limit,
            max_pages=max_pages,
        )
        chain_request = OptionChainRequest(
            collection_id=chain_collection_id,
            underlying_symbol=underlying_symbol,
            feed=feed,
            expiration_date=expiry,
            contract_type=contract_type,
            limit=chain_limit,
            max_pages=max_pages,
        )
        master_run = AlpacaOptionContractStore(contract_database).run(
            contract_request.request_id
        )
        chain_run = AlpacaOptionChainStore(chain_database).run(
            chain_request.request_id
        )
        if master_run is None or chain_run is None:
            raise AlpacaOptionSurfaceError
        surface = build_alpaca_option_surface(master_run, chain_run)
        _, created = publish_alpaca_option_surface(output_dir, surface)
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(surface, created),
        )
    except (
        AlpacaOptionChainStoreError,
        AlpacaOptionContractStoreError,
        AlpacaOptionSurfaceError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "bounded Alpaca option surface is invalid"
        ) from None
    if surface.status is not OptionSurfaceStatus.READY:
        raise typer.Exit(code=2)
    typer.echo("complete bounded Alpaca option surface")


def _report(surface: AlpacaOptionSurface, created: bool) -> str:
    return "\n".join(
        (
            "# Alpaca Option Surface",
            "",
            "> M6 shadow-only identity-joined derivative evidence; "
            "not a recommendation or order.",
            "",
            f"- result: {surface.status.value}",
            f"- underlying symbol: {surface.underlying_symbol}",
            f"- expiration date: {surface.expiration_date.isoformat()}",
            f"- contract type: {surface.contract_type.value}",
            f"- source feed: {surface.feed.value}",
            f"- master contracts: {surface.master_contract_count}",
            f"- chain snapshots: {surface.chain_snapshot_count}",
            f"- exact identity joins: {surface.joined_contract_count}",
            f"- snapshot coverage bps: {surface.snapshot_coverage_bps}",
            f"- open interest observations: {surface.open_interest_count}",
            f"- quote observations: {surface.quote_count}",
            f"- trade observations: {surface.trade_count}",
            f"- implied volatility observations: "
            f"{surface.implied_volatility_count}",
            f"- Greeks observations: {surface.greeks_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- provider operation: query-only local evidence join",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
