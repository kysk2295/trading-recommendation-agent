#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_research_agent_operations.py --help
# 3. Or make executable and run:
#      chmod +x run_research_agent_operations.py && ./run_research_agent_operations.py --help
# ──────────────────

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.research_agent_operations import (
    build_research_agent_operations_status,
    canonical_research_agent_operations_json,
)
from trading_agent.research_agent_operations_models import (
    ResearchAgentOperationsInputs,
    ResearchAgentOperationsLimits,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report the read-only six-family research operations status")
    parser.add_argument("--cycle-database", required=True, type=Path)
    parser.add_argument("--task-receipt-root", required=True, type=Path)
    parser.add_argument("--systematic-runs-root", required=True, type=Path)
    parser.add_argument("--as-of", type=_aware_datetime, default=dt.datetime.now(dt.UTC))
    parser.add_argument("--max-evidence-age-seconds", type=_positive, default=3_600)
    parser.add_argument("--daily-token-limit-per-family", type=_nonnegative, default=1_000_000)
    parser.add_argument("--daily-cost-limit-microusd-per-family", type=_nonnegative, default=100_000_000)
    parser.add_argument("--systematic-heavy-experiment-limit", type=_nonnegative, default=1)
    parser.add_argument("--storage-limit-bytes", type=_positive, default=1024**3)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = ResearchAgentOperationsInputs(
        cycle_database=args.cycle_database,
        task_receipt_root=args.task_receipt_root,
        systematic_runs_root=args.systematic_runs_root,
        as_of=args.as_of,
    )
    limits = ResearchAgentOperationsLimits(
        max_evidence_age_seconds=args.max_evidence_age_seconds,
        daily_token_limit_per_family=args.daily_token_limit_per_family,
        daily_cost_limit_microusd_per_family=args.daily_cost_limit_microusd_per_family,
        systematic_heavy_experiment_limit=args.systematic_heavy_experiment_limit,
        storage_limit_bytes=args.storage_limit_bytes,
    )
    status = build_research_agent_operations_status(inputs, limits)
    print(canonical_research_agent_operations_json(status))
    return 0 if status.status == "ready" else 1


def _positive(raw: str) -> int:
    value = _integer(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def _nonnegative(raw: str) -> int:
    value = _integer(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be nonnegative")
    return value


def _integer(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("value must be an integer") from None


def _aware_datetime(raw: str) -> dt.datetime:
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("as-of must be ISO-8601") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise argparse.ArgumentTypeError("as-of must include a UTC offset")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
