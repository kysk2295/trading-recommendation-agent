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

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_day_composite import (
    InvalidKrThemeDayCompositeError,
    KrThemeDayCompositeRegistrationRequest,
    register_kr_theme_day_composite,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_composite_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR Opportunity Manager와 day shadow 조합 사전등록")
    parser.add_argument("--day-strategy-version", required=True)
    parser.add_argument("--opportunity-strategy-version", required=True)
    parser.add_argument("--registered-at", type=dt.datetime.fromisoformat, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = register_kr_theme_day_composite(
            ExperimentLedgerStore(args.database),
            KrThemeDayCompositeRegistrationRequest(
                day_strategy_version=args.day_strategy_version,
                opportunity_strategy_version=args.opportunity_strategy_version,
                registered_at=args.registered_at,
            ),
        )
    except (
        InvalidKrThemeDayCompositeError,
        OSError,
        sqlite3.Error,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, result="blocked", created=False)
        return 1
    _write_report(args.output_dir, result="ready", created=result.created)
    return 0


def _write_report(output_dir: Path, *, result: str, created: bool) -> None:
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day composite registration",
                "",
                "> local append-only research authority only; provider와 주문을 호출하지 않습니다.",
                "",
                f"- result: {result}",
                f"- hypothesis created/reused: {int(created)}/{int(not created)}",
                "- component count: 2",
                "- order authority: false",
                "- external account/order mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
