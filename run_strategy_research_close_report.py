#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# uv run run_strategy_research_close_report.py --experiment-ledger <path> \
#   --hermes-ledger <path> --now <aware-ISO-8601>
# ───────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.strategy_research_close_report import (
    StrategyResearchCloseReportError,
    project_strategy_research_close_report,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project one persisted six-agent NYSE research-only close report to Hermes."
    )
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--hermes-ledger", type=Path, required=True)
    parser.add_argument("--now", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        now = dt.datetime.fromisoformat(args.now)
        if now.tzinfo is None or now.utcoffset() is None:
            raise StrategyResearchCloseReportError("close_report_now_must_be_aware")
        reader = ExperimentLedgerReader(args.experiment_ledger)
        with HermesDeliveryStore(args.hermes_ledger).writer() as writer:
            result = project_strategy_research_close_report(reader, writer, now)
    except (OSError, StrategyResearchCloseReportError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "error_class": type(error).__name__,
                    "profitability_claim": False,
                    "reason": str(error),
                    "status": "invalid",
                    "trading_authority": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "examined": result.examined,
                "inserted": result.inserted,
                "profitability_claim": False,
                "replayed": result.replayed,
                "status": "projected" if result.examined else "before_cutoff",
                "trading_authority": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
