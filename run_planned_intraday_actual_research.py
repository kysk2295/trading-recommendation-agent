#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from trading_agent.intraday_actual_research_plan import (
    run_planned_intraday_actual_research,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchPlanPaths,
    IntradayActualResearchRunSpec,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchStrategyBinding,
)
from trading_agent.private_immutable_file import read_private_text
from trading_agent.private_report import write_private_report
from trading_agent.strategy_factory import StrategyMode

REPORT_NAME = "intraday_actual_research_ko.md"
_SUCCESS_RECEIPT = re.compile(
    r"exit_code=0\ncompleted_at_epoch=[1-9][0-9]*\n"
)
_STRICT_CLOSEOUT_RESULTS = {"- result: recovered", "- result: replayed"}
_STRICT_CLOSEOUT_MARKERS = (
    "- failed cycle deletion: 0",
    "- quality gate relaxed: false",
    "- provider, credential, account, or order operation: 0",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze an exact run plan and execute strict actual intraday research"
    )
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--required-session-date",
        type=_session_date,
        action="append",
        required=True,
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--binding-dir", type=Path, required=True)
    parser.add_argument("--entitlement-contract", type=Path, required=True)
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
    parser.add_argument("--prerequisite-receipt", type=Path)
    parser.add_argument("--prerequisite-report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _require_closeout_prerequisite(
            args.prerequisite_receipt,
            args.prerequisite_report,
        )
        result = run_planned_intraday_actual_research(
            IntradayActualResearchRunSpec(
                run_key=args.run_key,
                session_dirs=tuple(path.resolve(strict=False) for path in args.session_dir),
                required_session_dates=tuple(args.required_session_date),
                strategy_bindings=tuple(args.strategy_binding),
                code_version=args.code_version,
                registered_at=args.registered_at,
                minimum_clean_sessions=args.minimum_clean_sessions,
                minimum_training_sessions=args.minimum_training_sessions,
                max_sessions=args.max_sessions,
                max_bars=args.max_bars,
                per_side_fee_bps=args.per_side_fee_bps,
                per_side_slippage_bps=args.per_side_slippage_bps,
                bootstrap_samples=args.bootstrap_samples,
                rss_limit_gib=args.rss_limit_gib,
                paths=IntradayActualResearchPlanPaths(
                    dataset_root=args.dataset_dir.resolve(strict=False),
                    binding_root=args.binding_dir.resolve(strict=False),
                    entitlement_contract=args.entitlement_contract.resolve(strict=False),
                    lane_registry=args.lane_registry.resolve(strict=False),
                    experiment_ledger=args.experiment_ledger.resolve(strict=False),
                    artifact_root=args.artifact_root.resolve(strict=False),
                    review_root=args.review_root.resolve(strict=False),
                ),
            ),
            plan_root=args.plan_dir.resolve(strict=False),
            queue_root=args.queue_dir.resolve(strict=False),
            observed_at=dt.datetime.now(dt.UTC),
        )
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Planned intraday actual research\n\n"
            "- result: blocked\n"
            "- external mutation: 0\n",
        )
        return 1

    actual = result.actual
    decisions = ", ".join(item.value for item in actual.loop.decisions)
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Planned intraday actual research\n\n"
        "- result: ready\n"
        + f"- run key: {result.plan.content.spec.run_key}\n"
        + f"- plan id: {result.plan.plan_id}\n"
        + f"- plan created: {str(result.plan_created).lower()}\n"
        + f"- queue created: {str(result.queue_created).lower()}\n"
        + f"- queue snapshot id: {result.plan.content.source_queue_snapshot_id}\n"
        + f"- selected sessions: {actual.catalog.dataset.session_count}\n"
        + f"- input sha256: {actual.catalog.dataset.input_sha256}\n"
        + f"- manifest sha256: {actual.binding.manifest_sha256}\n"
        + f"- foundations: {len(actual.binding.foundation_paths)}\n"
        + f"- trials: {actual.loop.trials_total}\n"
        + f"- experiment artifacts created: {actual.loop.experiment_artifacts_created}\n"
        + f"- review artifacts created: {actual.loop.review_artifacts_created}\n"
        + f"- reviewer decisions: {decisions}\n"
        + "- automatic state change: false\n"
        + "- external mutation: 0\n",
    )
    return 0


def _require_closeout_prerequisite(
    receipt: Path | None,
    report: Path | None,
) -> None:
    if (receipt is None) != (report is None):
        raise ValueError("prerequisite_paths_incomplete")
    if receipt is None or report is None:
        return
    receipt_payload = read_private_text(receipt)
    report_lines = read_private_text(report).splitlines()
    results = tuple(
        line for line in report_lines if line.startswith("- result: ")
    )
    if (
        _SUCCESS_RECEIPT.fullmatch(receipt_payload) is None
        or len(results) != 1
        or results[0] not in _STRICT_CLOSEOUT_RESULTS
        or any(report_lines.count(marker) != 1 for marker in _STRICT_CLOSEOUT_MARKERS)
    ):
        raise ValueError("closeout_prerequisite_invalid")


def _strategy_binding(value: str) -> IntradayResearchStrategyBinding:
    try:
        strategy, version, card_key = value.split(",")
        return IntradayResearchStrategyBinding(
            strategy=StrategyMode(strategy),
            strategy_version=version,
            queue_card_key=card_key,
        )
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "strategy binding must be STRATEGY,VERSION,CARD_SHA256"
        ) from None


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "required session date must be YYYY-MM-DD"
        ) from None


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError(
            "registered-at must be an ISO-8601 timestamp with offset"
        ) from None


if __name__ == "__main__":
    raise SystemExit(main())
