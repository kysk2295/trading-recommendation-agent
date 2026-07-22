#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisApiError, MissingKisCredentialsError
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_kr_session_calendar import (
    InvalidKisKrSessionCalendarError,
    project_kis_kr_session_calendar,
)
from trading_agent.kis_kr_session_calendar_client import (
    KisKrSessionCalendarClient,
    KisKrSessionCalendarFetchRequest,
    KisKrSessionCalendarTransportError,
    UnsafeKisKrSessionCalendarEndpointError,
    UnsafeKisKrSessionCalendarRedirectPolicyError,
)
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kis_kr_session_calendar_collection_ko.md"
KST = ZoneInfo("Asia/Seoul")
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KIS KR current-date session calendar GET-only collection")
    parser.add_argument("--calendar-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    requested_at = clock()
    try:
        base_date = requested_at.astimezone(KST).date()
        credentials = load_kis_credentials(KisMode.LIVE)
        with create_kis_client(KisMode.LIVE) as http_client:
            token = get_access_token(http_client, credentials, KisMode.LIVE)
            receipt = KisKrSessionCalendarClient(
                http_client,
                credentials,
                token,
                _clock=clock,
            ).fetch(
                KisKrSessionCalendarFetchRequest(
                    base_date=base_date,
                    requested_at=requested_at,
                )
            )
        snapshot = project_kis_kr_session_calendar(receipt)
        created = KisKrSessionCalendarStore(args.calendar_store).append(receipt, snapshot)
    except (
        FileNotFoundError,
        httpx2.HTTPError,
        InvalidKisKrSessionCalendarError,
        InvalidKisKrSessionCalendarStoreError,
        KisApiError,
        KisKrSessionCalendarTransportError,
        MissingKisCredentialsError,
        OSError,
        sqlite3.Error,
        UnsafeKisKrSessionCalendarEndpointError,
        UnsafeKisKrSessionCalendarRedirectPolicyError,
        UnsafeSecretFileError,
        ValueError,
    ):
        _write_report(args.output_dir, result="blocked", created=None, snapshot_id=None)
        return 1
    _write_report(
        args.output_dir,
        result="complete",
        created=created,
        snapshot_id=snapshot.snapshot_id,
    )
    print(snapshot.snapshot_id)
    return 0


def _write_report(
    output_dir: Path,
    *,
    result: str,
    created: bool | None,
    snapshot_id: str | None,
) -> None:
    details = (
        ()
        if created is None or snapshot_id is None
        else (
            f"snapshot 신규/재사용: {int(created)}/{int(not created)}",
            f"calendar snapshot: {snapshot_id}",
        )
    )
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KIS KR session calendar collection",
                "",
                "> current-date official GET-only evidence; account와 주문 endpoint를 호출하지 않습니다.",
                "",
                f"- result: {result}",
                *(f"- {detail}" for detail in details),
                "- external account/order mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
