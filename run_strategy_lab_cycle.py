#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.strategy_lab_kernel import StrategyLabFleet
from trading_agent.strategy_lab_models import STRATEGY_LAB_IDS, StrategyLabEvidenceBundle

type StrategyLabCliLab = dict[str, bool | float | int | str | list[str]]
type StrategyLabCliReport = dict[str, bool | int | str | list[StrategyLabCliLab]]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("iterations must be positive")
    return parsed


def _aware_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("as-of must be timezone-aware")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run legacy local-only StrategyLab diagnostic cycles; never the production six-agent runtime."
    )
    parser.add_argument("--evidence-bundle", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--iterations", type=_positive_int, default=1)
    parser.add_argument("--as-of", type=_aware_datetime, default=dt.datetime.now(dt.UTC))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        bundle = StrategyLabEvidenceBundle.model_validate_json(args.evidence_bundle.read_bytes())
        ledger = ExperimentLedgerStore(args.experiment_ledger.resolve(strict=False))
        fleet = StrategyLabFleet(ledger)
        for offset in range(args.iterations):
            _ = fleet.run_cycle(bundle, args.as_of + dt.timedelta(hours=offset))
        report = _complete_report(ledger, args.iterations)
    except (OSError, RuntimeError, TypeError, ValidationError, ValueError, sqlite3.Error):
        blocked = {"reason": "evidence_or_trace_invalid", "status": "blocked"}
        print(json.dumps(blocked, separators=(",", ":"), sort_keys=True))
        return 1
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


def _complete_report(ledger: ExperimentLedgerStore, iterations: int) -> StrategyLabCliReport:
    labs: list[StrategyLabCliLab] = []
    for lab_id in STRATEGY_LAB_IDS:
        trace = ledger.strategy_lab_trace(lab_id)
        protocols = ledger.strategy_lab_protocols(lab_id)
        latest_node = trace[-1]
        latest_protocol = protocols[-1]
        labs.append(
            {
                "dataset_id": latest_protocol.body.dataset_id,
                "evidence_mode": latest_protocol.body.evidence_mode.value,
                "feedback_linked": len(trace) > 1
                and latest_node.body.parent_node_id == trace[-2].node_id,
                "lab_id": lab_id.value,
                "latest_hypothesis_id": latest_protocol.body.hypothesis.hypothesis_id,
                "latest_outcome": latest_node.body.result.outcome.value,
                "latest_protocol_id": latest_protocol.protocol_id,
                "next_adaptation": latest_node.body.feedback.value,
                "reason_codes": list(latest_node.body.result.reason_codes),
                "selected_observations": latest_node.body.result.selected_observations,
                "selected_threshold": latest_protocol.body.selected_threshold,
                "trace_depth": len(trace),
            }
        )
    return {
        "completed_iterations": iterations,
        "lab_count": len(labs),
        "labs": labs,
        "order_authority": False,
        "status": "complete",
        "trading_mutation": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
