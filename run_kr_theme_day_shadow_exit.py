#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
)
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_cycle import (
    InvalidKrThemeDayShadowExitCycleError,
    KrThemeDayShadowExitCycleRequest,
    KrThemeDayShadowExitCycleResult,
    KrThemeDayShadowExitStores,
    run_kr_theme_day_shadow_exit_cycle,
)
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_shadow_exit_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day local raw-evidence shadow exit projection")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--receipt-store", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--exit-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_kr_theme_day_shadow_exit_cycle(
            KrThemeDayShadowExitStores(
                receipts=KisKrMarketReceiptStore(args.receipt_store),
                entries=KrThemeDayShadowEntryStore(args.entry_store),
                exits=KrThemeDayShadowExitStore(args.exit_store),
            ),
            KrThemeDayShadowExitCycleRequest(
                trial_id=args.trial_id,
                evaluated_at=dt.datetime.fromisoformat(args.evaluated_at),
            ),
        )
    except (
        InvalidKisKrMarketReceiptStoreError,
        InvalidKrThemeDayShadowEntryStoreError,
        InvalidKrThemeDayShadowExitCycleError,
        InvalidKrThemeDayShadowExitStoreError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result)
    return 0


def _write_report(output_dir: Path, result: KrThemeDayShadowExitCycleResult | None) -> None:
    status = "blocked" if result is None else "complete"
    counts = (
        ()
        if result is None
        else (
            "terminal/open/pending/new: "
            f"{result.terminal_entry_count}/{result.open_entry_count}/"
            f"{result.pending_entry_count}/{result.created_exit_count}",
        )
    )
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day shadow exit cycle",
                "",
                "> local raw-evidence shadow projection; account와 주문 endpoint를 호출하지 않습니다.",
                "",
                f"- 결과: {status}",
                *(f"- {item}" for item in counts),
                "- order authority: false",
                "- external mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
