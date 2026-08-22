#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.12,<3"]
# ///
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from trading_agent.us_day_champion_bootstrap import (
    UsDayChampionBootstrapError,
    UsDayChampionBootstrapRequest,
    bootstrap_us_day_champion,
    plan_us_day_champion_bootstrap,
)

_BLOCKED: Final = {
    "order_authority": "0",
    "paper_trading_enabled": "0",
    "reason": "champion_bootstrap_invalid",
    "status": "blocked",
}


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    parsed = parser.parse_args(arguments)
    try:
        request = UsDayChampionBootstrapRequest(
            strategy_manifest=parsed.strategy_manifest,
            experiment_ledger=parsed.experiment_ledger,
            version_store=parsed.version_store,
            reasoning_model_id=parsed.reasoning_model_id,
            prompt_policy=parsed.prompt_policy,
            tool_policy=parsed.tool_policy,
            memory_policy=parsed.memory_policy,
            review_evidence=parsed.review_evidence,
            receipt_root=parsed.receipt_root,
            created_at=dt.datetime.fromisoformat(parsed.created_at),
            created_session_date=dt.date.fromisoformat(parsed.created_session_date),
        )
        if parsed.mode == "preflight":
            plan = plan_us_day_champion_bootstrap(request)
            payload = _ready(plan.version.version_id, False, False)
        else:
            result = bootstrap_us_day_champion(request)
            payload = _ready(
                result.receipt.version.version_id,
                result.version_created,
                result.receipt_created,
            )
    except (UsDayChampionBootstrapError, ValidationError, ValueError):
        print(json.dumps(_BLOCKED, separators=(",", ":"), sort_keys=True))
        return 2
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


def _ready(version_id: str, version_created: bool, receipt_created: bool) -> dict[str, str]:
    return {
        "order_authority": "0",
        "paper_trading_enabled": "0",
        "receipt_created": "1" if receipt_created else "0",
        "status": "ready",
        "version_created": "1" if version_created else "0",
        "version_id": version_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or register the authority-free US Day initial Champion."
    )
    parser.add_argument("mode", choices=("preflight", "bootstrap"))
    parser.add_argument("--strategy-manifest", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--version-store", type=Path, required=True)
    parser.add_argument("--reasoning-model-id", required=True)
    parser.add_argument("--prompt-policy", type=Path, required=True)
    parser.add_argument("--tool-policy", type=Path, required=True)
    parser.add_argument("--memory-policy", type=Path, required=True)
    parser.add_argument("--review-evidence", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--created-session-date", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
