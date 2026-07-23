#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_contract_catalog.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_contract_client import (
    AlpacaOptionContractClient,
)
from trading_agent.alpaca_option_contract_collection import (
    AlpacaOptionContractTransportError,
    collect_alpaca_option_contracts,
)
from trading_agent.alpaca_option_contract_models import (
    AlpacaOptionContractError,
    OptionCatalogStatus,
    OptionContractCatalogRequest,
    OptionContractCatalogRun,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.alpaca_paper_config import (
    create_alpaca_paper_read_client as create_alpaca_option_contract_http_client,
)
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_contract_catalog_ko.md"


class _FixturePageFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        if page_index != 0 or page_token is not None:
            raise AlpacaOptionContractTransportError
        return OptionContractRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/json",
            raw_payload=self._path.read_bytes(),
        )


def main(
    collection_id: Annotated[str, typer.Option()],
    underlying_symbol: Annotated[str, typer.Option()],
    expiration_date: Annotated[str, typer.Option()],
    contract_type: Annotated[OptionContractType, typer.Option()],
    database: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    limit: Annotated[int, typer.Option()] = 100,
    max_pages: Annotated[int, typer.Option()] = 2,
    fixture_page: Annotated[Path | None, typer.Option()] = None,
    credentials_path: Annotated[
        Path,
        typer.Option(),
    ] = DEFAULT_ALPACA_SECRET_PATH,
) -> None:
    try:
        request = OptionContractCatalogRequest(
            collection_id=collection_id,
            underlying_symbol=underlying_symbol,
            expiration_date=dt.date.fromisoformat(expiration_date),
            contract_type=contract_type,
            limit=limit,
            max_pages=max_pages,
        )
        store = AlpacaOptionContractStore(database)
        existing = store.run(request.request_id)
        if existing is not None:
            result = collect_alpaca_option_contracts(
                _FixturePageFetcher(Path("/nonexistent")),
                store,
                request,
            )
            network_access = "0"
        else:
            store.preflight_write()
            if fixture_page is not None:
                result = collect_alpaca_option_contracts(
                    _FixturePageFetcher(fixture_page),
                    store,
                    request,
                )
                network_access = "0"
            else:
                credentials = load_private_alpaca_credentials(
                    credentials_path
                )
                with create_alpaca_option_contract_http_client() as client:
                    result = collect_alpaca_option_contracts(
                        AlpacaOptionContractClient(client, credentials),
                        store,
                        request,
                    )
                network_access = "GET-only"
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(
                result.run,
                result.replayed,
                network_access,
            ),
        )
    except (
        AlpacaOptionContractError,
        AlpacaOptionContractStoreError,
        AlpacaOptionContractTransportError,
        InvalidPrivateStableReportError,
        OSError,
        PrivateAlpacaCredentialsError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "bounded Alpaca option contract catalog is invalid"
        ) from None
    if result.run.status is not OptionCatalogStatus.SUCCESS:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca option contract catalog")


def _report(
    run: OptionContractCatalogRun,
    replayed: bool,
    network_access: str,
) -> str:
    failure = (
        "none"
        if run.failure_code is None
        else run.failure_code.value
    )
    return "\n".join(
        (
            "# Alpaca Option Contract Catalog",
            "",
            "> M6 GET-only contract master; not a recommendation or order.",
            "",
            f"- result: {run.status.value}",
            f"- failure code: {failure}",
            f"- replayed: {'yes' if replayed else 'no'}",
            f"- raw response pages: {len(run.receipt_ids)}",
            f"- option contracts: {len(run.contracts)}",
            f"- network access: {network_access}",
            "- provider operation: GET-only",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
