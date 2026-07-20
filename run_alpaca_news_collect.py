#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Final

import typer

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH, create_alpaca_news_http_client
from trading_agent.alpaca_news_client import AlpacaNewsClient, AlpacaNewsTransportError
from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_fixture import AlpacaNewsFixtureError, load_alpaca_news_fixture
from trading_agent.alpaca_news_models import (
    AlpacaNewsContractError,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRunStatus,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore, AlpacaNewsStoreError
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_news_collection_ko.md"


class _ReplayOnlyFetcher:
    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        _ = request, page_index, page_token
        raise AlpacaNewsTransportError


def main(
    collection_id: str | None = None,
    symbols: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    database: str = "outputs/us_news/alpaca_news.sqlite3",
    output_dir: str = "outputs/us_news/alpaca-news-latest",
    limit: int = 50,
    max_pages: int = 8,
    fixture_manifest: str | None = None,
    credentials_path: str | None = None,
) -> None:
    if fixture_manifest is not None and credentials_path is not None:
        raise typer.BadParameter("fixture mode cannot use a credentials file")
    try:
        request = _request(collection_id, symbols, start_at, end_at, limit, max_pages)
        database_path, report_path = _distinct_paths(
            Path(database),
            Path(output_dir) / REPORT_NAME,
        )
        store = AlpacaNewsStore(database_path)
        existing = store.run(request.request_id)
        if existing is not None:
            result = collect_alpaca_news(_ReplayOnlyFetcher(), store, request)
            access = "0"
        else:
            store.preflight_write()
            if fixture_manifest is not None:
                fetcher = load_alpaca_news_fixture(Path(fixture_manifest))
                result = collect_alpaca_news(fetcher, store, request)
                access = "0"
            else:
                secret_path = DEFAULT_ALPACA_SECRET_PATH if credentials_path is None else Path(credentials_path)
                credentials = load_private_alpaca_credentials(secret_path)
                with create_alpaca_news_http_client() as http_client:
                    result = collect_alpaca_news(
                        AlpacaNewsClient(http_client, credentials),
                        store,
                        request,
                    )
                access = "GET-only"
        receipts = store.receipts(request.request_id)
        write_private_stable_report(
            report_path,
            _report(
                result.run.status.value,
                result.replayed,
                len(request.symbols),
                int((request.end_at - request.start_at).total_seconds()),
                result.run.page_count,
                result.run.article_count,
                sum(len(item.response.raw_payload) for item in receipts),
                access,
            ),
        )
    except (
        AlpacaNewsContractError,
        AlpacaNewsFixtureError,
        AlpacaNewsStoreError,
        AlpacaNewsTransportError,
        InvalidPrivateStableReportError,
        OSError,
        PrivateAlpacaCredentialsError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("Alpaca news collection state is invalid") from None
    if result.run.status is not AlpacaNewsRunStatus.SUCCESS:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca news collection")


def _request(
    collection_id: str | None,
    symbols: str | None,
    start_at: str | None,
    end_at: str | None,
    limit: int,
    max_pages: int,
) -> AlpacaNewsRequest:
    values = tuple(item.strip().upper() for item in (symbols or "").split(",") if item.strip())
    try:
        return AlpacaNewsRequest(
            collection_id=collection_id or "",
            symbols=values,
            start_at=_time(start_at),
            end_at=_time(end_at),
            limit=limit,
            max_pages=max_pages,
        )
    except (TypeError, ValueError):
        raise typer.BadParameter("bounded Alpaca news request is invalid") from None


def _time(value: str | None) -> dt.datetime:
    if value is None:
        raise ValueError
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _distinct_paths(database: Path, report: Path) -> tuple[Path, Path]:
    try:
        left = absolute_private_path(database)
        right = absolute_private_path(report)
        if left == right or (left.exists() and right.exists() and os.path.samestat(left.stat(), right.stat())):
            raise ValueError
        return left, right
    except (OSError, RuntimeError, ValueError):
        raise typer.BadParameter("database and report paths must be distinct and valid") from None


def _report(
    status: str,
    replayed: bool,
    symbol_count: int,
    window_seconds: int,
    page_count: int,
    article_count: int,
    raw_bytes: int,
    network_access: str,
) -> str:
    return "\n".join(
        (
            "# Alpaca News Collection",
            "",
            "> Licensed news evidence only; not a recommendation or profitability result.",
            "",
            f"- result: {status}",
            f"- replayed: {'yes' if replayed else 'no'}",
            f"- requested symbols: {symbol_count}",
            f"- bounded window seconds: {window_seconds}",
            f"- raw response pages: {page_count}",
            f"- raw response bytes: {raw_bytes}",
            f"- articles: {article_count}",
            f"- network access: {network_access}",
            "- provider operation: GET-only",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
