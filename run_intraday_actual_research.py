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

from trading_agent.intraday_actual_research import run_intraday_actual_research
from trading_agent.intraday_actual_research_models import (
    IntradayActualResearchPaths,
    IntradayActualResearchRequest,
)
from trading_agent.intraday_research_input_binding_models import IntradayResearchStrategyBinding
from trading_agent.private_report import write_private_report
from trading_agent.strategy_factory import StrategyMode

REPORT_NAME = "intraday_actual_research_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run strict session catalog, actual input binding, walk-forward, and independent review"
    )
    parser.add_argument("--session-dir", type=Path, action="append", required=True)
    parser.add_argument("--required-session-date", type=_session_date, action="append", required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--binding-dir", type=Path, required=True)
    parser.add_argument("--entitlement-contract", type=Path, required=True)
    parser.add_argument("--source-queue-artifact", type=Path, required=True)
    parser.add_argument(
        "--strategy-binding",
        type=_strategy_binding,
        action="append",
        required=True,
        metavar="STRATEGY,VERSION,CARD_SHA256",
    )
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--registered-at", type=_aware_datetime, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-clean-sessions", type=int, default=1)
    parser.add_argument("--minimum-training-sessions", type=int, default=0)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--max-bars", type=int, default=100_000)
    parser.add_argument("--per-side-fee-bps", type=int, default=5)
    parser.add_argument("--per-side-slippage-bps", type=int, default=15)
    parser.add_argument("--bootstrap-samples", type=int, default=1_000)
    parser.add_argument("--rss-limit-gib", type=float, default=9.5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_intraday_actual_research(
            IntradayActualResearchRequest(
                session_dirs=tuple(args.session_dir),
                required_session_dates=tuple(args.required_session_date),
                strategy_bindings=tuple(args.strategy_binding),
                code_version=args.code_version,
                registered_at=args.registered_at,
                observed_at=dt.datetime.now(dt.UTC),
                minimum_clean_sessions=args.minimum_clean_sessions,
                minimum_training_sessions=args.minimum_training_sessions,
                max_sessions=args.max_sessions,
                max_bars=args.max_bars,
                per_side_fee_bps=args.per_side_fee_bps,
                per_side_slippage_bps=args.per_side_slippage_bps,
                bootstrap_samples=args.bootstrap_samples,
                rss_limit_gib=args.rss_limit_gib,
                paths=IntradayActualResearchPaths(
                    dataset_root=args.dataset_dir,
                    binding_root=args.binding_dir,
                    entitlement_contract=args.entitlement_contract,
                    source_queue_artifact=args.source_queue_artifact,
                    lane_registry=args.lane_registry,
                    experiment_ledger=args.experiment_ledger,
                    artifact_root=args.artifact_root,
                    review_root=args.review_root,
                ),
            )
        )
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday actual research\n\n- result: blocked\n- external mutation: 0\n",
        )
        return 1
    decisions = ", ".join(item.value for item in result.loop.decisions)
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday actual research\n\n"
        "- result: ready\n"
        + f"- candidate sessions: {result.catalog.candidate_sessions}\n"
        + f"- selected sessions: {result.catalog.dataset.session_count}\n"
        + f"- blocked sessions: {result.catalog.blocked_sessions}\n"
        + f"- input sha256: {result.catalog.dataset.input_sha256}\n"
        + f"- manifest sha256: {result.binding.manifest_sha256}\n"
        + f"- foundations: {len(result.binding.foundation_paths)}\n"
        + f"- trials: {result.loop.trials_total}\n"
        + f"- experiment artifacts created: {result.loop.experiment_artifacts_created}\n"
        + f"- review artifacts created: {result.loop.review_artifacts_created}\n"
        + f"- reviewer decisions: {decisions}\n"
        + "- automatic state change: false\n"
        + "- external mutation: 0\n",
    )
    return 0


def _strategy_binding(value: str) -> IntradayResearchStrategyBinding:
    try:
        strategy, version, card_key = value.split(",")
        return IntradayResearchStrategyBinding(
            strategy=StrategyMode(strategy),
            strategy_version=version,
            queue_card_key=card_key,
        )
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("strategy binding must be STRATEGY,VERSION,CARD_SHA256") from None


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("required session date must be YYYY-MM-DD") from None


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("registered-at must be an ISO-8601 timestamp with offset") from None


if __name__ == "__main__":
    raise SystemExit(main())
