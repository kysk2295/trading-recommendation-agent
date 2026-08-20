from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.private_immutable_file import read_private_text
from trading_agent.us_forward_shadow_artifacts import UsForwardShadowArtifactStore
from trading_agent.us_forward_shadow_models import UsForwardShadowTick
from trading_agent.us_forward_shadow_runtime import (
    run_us_forward_shadow_tick,
    validate_current_us_forward_shadow_tick,
)
from trading_agent.us_forward_shadow_services import UsForwardShadowServices


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one research-only US completed-bar Forward Shadow tick."
    )
    parser.add_argument("--tick", type=Path, required=True, help="Private 0600 tick snapshot JSON")
    parser.add_argument("--ledger", type=Path, required=True, help="Append-only experiment ledger")
    parser.add_argument("--generated-artifacts", type=Path, required=True)
    parser.add_argument("--shadow-artifacts", type=Path, required=True)
    parser.add_argument("--task-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] | None = None,
) -> int:
    args = parse_args(argv)
    try:
        tick = UsForwardShadowTick.model_validate_json(read_private_text(args.tick.expanduser().absolute()))
        evaluation_at = dt.datetime.now(dt.UTC) if clock is None else clock()
        checked = validate_current_us_forward_shadow_tick(tick, evaluation_at=evaluation_at)
        runtime = resolve_generated_strategy_runtime(Path(sys.executable))
        services = UsForwardShadowServices(
            ledger=ExperimentLedgerStore(args.ledger),
            generated_artifacts=GeneratedStrategyArtifactStore(args.generated_artifacts, runtime),
            shadow_artifacts=UsForwardShadowArtifactStore(args.shadow_artifacts),
            task_root=args.task_root,
        )
        result = run_us_forward_shadow_tick(checked, services, evaluation_at=evaluation_at)
    except (OSError, RuntimeError, TypeError, ValidationError, ValueError):
        print(json.dumps({"reason_code": "input_or_runtime_blocked", "status": "blocked"}, sort_keys=True))
        return 2
    print(json.dumps(result.model_dump(mode="json"), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
