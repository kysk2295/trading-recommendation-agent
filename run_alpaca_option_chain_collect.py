#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_chain_collect.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.alpaca_option_chain_client import (
    AlpacaOptionChainClient,
    AlpacaOptionChainTransportError,
    create_alpaca_option_chain_http_client,
)
from trading_agent.alpaca_option_chain_collection import (
    collect_alpaca_option_chain,
)
from trading_agent.alpaca_option_chain_models import (
    AlpacaOptionChainError,
    OptionChainRawResponse,
    OptionChainRequest,
    OptionChainStatus,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_chain_collection_ko.md"


class _FixturePageFetcher:
    __slots__ = ("_clock", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._clock = lambda: dt.datetime.now(dt.UTC)

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        if page_index != 0 or page_token is not None:
            raise AlpacaOptionChainTransportError
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=self._clock(),
            status_code=200,
            content_type="application/json",
            raw_payload=self._path.read_bytes(),
        )


def main(
    collection_id: Annotated[str, typer.Option()],
    underlying_symbol: Annotated[str, typer.Option()],
    feed: Annotated[OptionFeed, typer.Option()],
    expiration_date: Annotated[str, typer.Option()],
    contract_type: Annotated[OptionContractType, typer.Option()],
    database: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    limit: Annotated[int, typer.Option()] = 1_000,
    max_pages: Annotated[int, typer.Option()] = 2,
    fixture_page: Annotated[Path | None, typer.Option()] = None,
    credentials_path: Annotated[Path, typer.Option()] = DEFAULT_ALPACA_SECRET_PATH,
) -> None:
    try:
        request = OptionChainRequest(
            collection_id=collection_id,
            underlying_symbol=underlying_symbol,
            feed=feed,
            expiration_date=dt.date.fromisoformat(expiration_date),
            contract_type=contract_type,
            limit=limit,
            max_pages=max_pages,
        )
        store = AlpacaOptionChainStore(database)
        existing = store.run(request.request_id)
        if existing is not None:
            result = collect_alpaca_option_chain(
                _FixturePageFetcher(Path("/nonexistent")),
                store,
                request,
            )
            access = "0"
        else:
            store.preflight_write()
            if fixture_page is not None:
                result = collect_alpaca_option_chain(
                    _FixturePageFetcher(fixture_page),
                    store,
                    request,
                )
                access = "0"
            else:
                credentials = load_private_alpaca_credentials(credentials_path)
                with create_alpaca_option_chain_http_client() as client:
                    result = collect_alpaca_option_chain(
                        AlpacaOptionChainClient(client, credentials),
                        store,
                        request,
                    )
                access = "GET-only"
        receipts = store.receipts(request.request_id)
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(
                result.run.status.value,
                result.replayed,
                request,
                len(result.run.snapshots),
                len(receipts),
                sum(len(item.raw_payload) for item in receipts),
                access,
                (
                    None
                    if result.run.failure_code is None
                    else result.run.failure_code.value
                ),
            ),
        )
    except (
        AlpacaOptionChainError,
        AlpacaOptionChainStoreError,
        AlpacaOptionChainTransportError,
        InvalidPrivateStableReportError,
        OSError,
        PrivateAlpacaCredentialsError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "bounded Alpaca option chain collection is invalid"
        ) from None
    if result.run.status is not OptionChainStatus.SUCCESS:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca option chain collection")


def _report(
    status: str,
    replayed: bool,
    request: OptionChainRequest,
    snapshot_count: int,
    page_count: int,
    raw_bytes: int,
    network_access: str,
    failure_code: str | None,
) -> str:
    failure = "none" if failure_code is None else failure_code
    return "\n".join(
        (
            "# Alpaca Option Chain Collection",
            "",
            "> M6 read-only derivatives evidence; not a recommendation or order.",
            "",
            f"- result: {status}",
            f"- failure code: {failure}",
            f"- replayed: {'yes' if replayed else 'no'}",
            f"- underlying symbol: {request.underlying_symbol}",
            f"- expiration date: {request.expiration_date.isoformat()}",
            f"- contract type: {request.contract_type.value}",
            f"- source feed: {request.feed.value}",
            f"- raw response pages: {page_count}",
            f"- raw response bytes: {raw_bytes}",
            f"- option snapshots: {snapshot_count}",
            f"- network access: {network_access}",
            "- provider operation: GET-only",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
