#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import httpx2

from scr_backtest.kis_intraday import KisApiError, MissingKisCredentialsError
from trading_agent.kis_auth import (
    DEFAULT_SECRET_PATH,
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_overseas_futures_client import (
    KisOverseasFuturesClient,
    KisOverseasFuturesTransportError,
)
from trading_agent.kis_overseas_futures_collection import (
    KisFuturesQuoteCollectionResult,
    collect_kis_overseas_futures_quotes,
)
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
    KisFuturesQuoteStatus,
)
from trading_agent.kis_overseas_futures_store import (
    KisOverseasFuturesStore,
    KisOverseasFuturesStoreError,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME = "kis_overseas_futures_quotes_ko.md"


class _ReplayOnlyFetcher:
    def fetch(
        self,
        request: KisFuturesQuoteRequest,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse:
        del request, symbol
        raise KisOverseasFuturesTransportError


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KIS 공식 GET으로 bounded 해외선물 현재가를 raw-first 수집"
    )
    parser.add_argument("--root-symbol", required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--credentials-path",
        type=Path,
        default=DEFAULT_SECRET_PATH,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = KisFuturesQuoteRequest(
            root_symbol=args.root_symbol,
            symbols=tuple(
                value.strip().upper()
                for value in args.symbols.split(",")
                if value.strip()
            ),
        )
        store = KisOverseasFuturesStore(args.database)
        if store.run(request.request_id) is not None:
            result = collect_kis_overseas_futures_quotes(
                _ReplayOnlyFetcher(),
                store,
                request,
            )
            network_access = "0"
        else:
            credentials = load_kis_credentials(
                KisMode.LIVE,
                args.credentials_path,
            )
            with create_kis_client(KisMode.LIVE) as http_client:
                token = get_access_token(
                    http_client,
                    credentials,
                    KisMode.LIVE,
                )
                result = collect_kis_overseas_futures_quotes(
                    KisOverseasFuturesClient(
                        http_client,
                        credentials,
                        token,
                    ),
                    store,
                    request,
                )
            network_access = "GET-only"
        write_private_stable_report(
            args.output_dir / REPORT_NAME,
            _report(result, network_access),
        )
    except (
        FileNotFoundError,
        httpx2.HTTPError,
        InvalidPrivateStableReportError,
        KisApiError,
        KisOverseasFuturesStoreError,
        KisOverseasFuturesTransportError,
        MissingKisCredentialsError,
        OSError,
        PermissionError,
        UnsafeSecretFileError,
        TypeError,
        ValueError,
    ):
        return 1
    return 0 if result.run.status is KisFuturesQuoteStatus.SUCCESS else 1


def _report(
    result: KisFuturesQuoteCollectionResult,
    network_access: str,
) -> str:
    run = result.run
    exchanges = {item.exchange for item in run.quotes}
    currencies = {item.currency for item in run.quotes}
    expirations = tuple(item.expiration_date for item in run.quotes)
    return "\n".join(
        (
            "# KIS Overseas Futures Quotes",
            "",
            "> Official read-only quote evidence; not a recommendation or order.",
            "",
            f"- result: {run.status.value}",
            f"- failure: {run.failure.value if run.failure else 'none'}",
            f"- replayed: {'yes' if result.replayed else 'no'}",
            f"- requested contracts: {len(run.request.symbols)}",
            f"- raw receipts: {len(run.receipt_ids)}",
            f"- canonical quotes: {len(run.quotes)}",
            f"- exchange count: {len(exchanges)}",
            f"- currency count: {len(currencies)}",
            f"- expiration count: {len(set(expirations))}",
            f"- network access: {network_access}",
            "- provider operation: GET-only",
            "- broker, account, position, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
