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
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
)
from trading_agent.kr_theme_day_intraday import (
    InvalidKrThemeDayIntradayError,
    KrThemeDayIntradayEntryRequest,
    run_kr_theme_day_intraday_entry,
)
from trading_agent.kr_theme_day_intraday_io import (
    InvalidKrThemeDayOpportunitySourceError,
    kr_theme_day_opportunity_sha256,
    load_exact_kr_theme_opportunity,
)
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_intraday_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day read-only evidence shadow projection")
    parser.add_argument("--opportunity-outbox", type=Path, required=True)
    parser.add_argument("--opportunity-id", required=True)
    parser.add_argument("--opportunity-sha256", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--filled-at", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt-store", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        opportunity = load_exact_kr_theme_opportunity(args.opportunity_outbox, args.opportunity_id)
        if kr_theme_day_opportunity_sha256(opportunity) != args.opportunity_sha256:
            raise InvalidKrThemeDayOpportunitySourceError
        outcome = run_kr_theme_day_intraday_entry(
            ExperimentLedgerStore(args.database),
            KisKrMarketReceiptStore(args.receipt_store),
            KrThemeDayShadowEntryStore(args.entry_store),
            KrThemeDayIntradayEntryRequest(
                opportunity=opportunity,
                producer_strategy_version=args.strategy_version,
                evaluated_at=dt.datetime.fromisoformat(args.evaluated_at),
                filled_at=dt.datetime.fromisoformat(args.filled_at),
                max_slippage_bps=Decimal("20"),
            ),
        )
    except (
        InvalidKisKrMarketReceiptStoreError,
        InvalidKrThemeDayIntradayError,
        InvalidKrThemeDayOpportunitySourceError,
        InvalidKrThemeDayShadowEntryStoreError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, "blocked")
        return 1
    _write_report(args.output_dir, outcome.status.value)
    return 0


def _write_report(output_dir: Path, result: str) -> None:
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day intraday shadow projection",
                "",
                "> local raw-evidence projection only; provider credential, account와 주문을 호출하지 않습니다.",
                "",
                f"- 결과: {result}",
                "- slippage bps: 20",
                "- order authority: false",
                "- external mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
