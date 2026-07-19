#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import httpx2
from pydantic import ValidationError

from scr_backtest.kis_intraday import KisApiError, MissingKisCredentialsError
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_kr_market_client import (
    KisKrMarketClient,
    KisKrMarketTransportError,
    UnsafeKisKrMarketEndpointError,
    UnsafeKisKrMarketRedirectPolicyError,
)
from trading_agent.kis_kr_market_collection import (
    InvalidKisKrMarketCollectionError,
    KisKrMarketCollectionPhase,
    KisKrMarketCollectionRequest,
    KisKrMarketCollectionResult,
    collect_kis_kr_market_receipts,
)
from trading_agent.kis_kr_market_fixture import (
    InvalidKisKrMarketFixtureError,
    load_kis_kr_market_fixture,
)
from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
)
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_session_runtime_gate import (
    InvalidKrSessionRuntimeError,
    require_open_kr_eod_session,
    require_open_kr_runtime_session,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kis_kr_market_collection_ko.md"


@dataclass(frozen=True, slots=True)
class _CollectionReport:
    status: str
    provider_mode: str
    phase: KisKrMarketCollectionPhase
    result: KisKrMarketCollectionResult | None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KIS KR current-session GET-only market receipt collection")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--calendar-store", type=Path, required=True)
    parser.add_argument("--calendar-snapshot-id", required=True)
    parser.add_argument("--receipt-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixture-manifest", type=Path)
    parser.add_argument("--eod-minute", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    provider_mode = "production"
    phase = KisKrMarketCollectionPhase.EOD_MINUTE if args.eod_minute else KisKrMarketCollectionPhase.INTRADAY
    try:
        if args.fixture_manifest is not None:
            provider_mode = "fixture"
            loaded = load_kis_kr_market_fixture(args.fixture_manifest)
            if loaded.manifest.symbol != args.symbol or loaded.manifest.phase is not phase:
                raise InvalidKisKrMarketFixtureError
            observed_at = loaded.manifest.requested_at
            session_date = _session_date(args, observed_at)
            result = collect_kis_kr_market_receipts(
                loaded.fetcher,
                KisKrMarketReceiptStore(args.receipt_store),
                KisKrMarketCollectionRequest(args.symbol, session_date, lambda: observed_at, phase),
            )
        else:
            observed_at = _current_time()
            session_date = _session_date(args, observed_at)
            credentials = load_kis_credentials(KisMode.LIVE)
            with create_kis_client(KisMode.LIVE) as http_client:
                token = get_access_token(http_client, credentials, KisMode.LIVE)
                result = collect_kis_kr_market_receipts(
                    KisKrMarketClient(http_client, credentials, token),
                    KisKrMarketReceiptStore(args.receipt_store),
                    KisKrMarketCollectionRequest(args.symbol, session_date, _current_time, phase),
                )
    except (
        FileNotFoundError,
        httpx2.HTTPError,
        InvalidKisKrMarketCollectionError,
        InvalidKisKrMarketFixtureError,
        InvalidKisKrMarketReceiptStoreError,
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrSessionRuntimeError,
        KisApiError,
        KisKrMarketTransportError,
        MissingKisCredentialsError,
        OSError,
        PermissionError,
        sqlite3.Error,
        TypeError,
        UnsafeKisKrMarketEndpointError,
        UnsafeKisKrMarketRedirectPolicyError,
        UnsafeSecretFileError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, _CollectionReport("blocked", provider_mode, phase, None))
        return 1
    _write_report(args.output_dir, _CollectionReport("complete", provider_mode, phase, result))
    return 0


def _session_date(args: argparse.Namespace, observed_at: dt.datetime) -> dt.date:
    matches = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(args.calendar_store).snapshots()
        if snapshot.snapshot_id == args.calendar_snapshot_id
    )
    if len(matches) != 1:
        raise InvalidKrSessionRuntimeError
    phase = KisKrMarketCollectionPhase.EOD_MINUTE if args.eod_minute else KisKrMarketCollectionPhase.INTRADAY
    match phase:
        case KisKrMarketCollectionPhase.INTRADAY:
            return require_open_kr_runtime_session(matches[0], observed_at)
        case KisKrMarketCollectionPhase.EOD_MINUTE:
            return require_open_kr_eod_session(matches[0], observed_at)
        case unreachable:
            assert_never(unreachable)


def _current_time() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _write_report(
    output_dir: Path,
    report: _CollectionReport,
) -> None:
    counts = (
        ()
        if report.result is None
        else (
            "receipt 신규/재사용: "
            f"{report.result.created_count}/{report.result.receipt_count - report.result.created_count}",
        )
    )
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KIS KR market receipt collection",
                "",
                "> current-session GET-only raw evidence; account와 주문 endpoint를 호출하지 않습니다.",
                "",
                f"- 결과: {report.status}",
                f"- provider mode: {report.provider_mode}",
                f"- collection phase: {report.phase.value}",
                *(f"- {item}" for item in counts),
                "- order authority: false",
                "- external mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
