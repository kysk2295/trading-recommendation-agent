#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run python run_kr_day_close_service.py --help
# ─────────────────

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.kr_day_close_service import KrDayCloseRuntime, run_kr_day_close_service
from trading_agent.kr_day_close_service_config import (
    InvalidKrDayCloseServiceConfigError,
    load_kr_day_close_service_config,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize one official XKRX research session.")
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    runtime: KrDayCloseRuntime | None = None,
) -> int:
    args = parse_args(argv)
    try:
        config = load_kr_day_close_service_config(args.config)
    except InvalidKrDayCloseServiceConfigError:
        _emit({"complete": False, "reason": "config_invalid", "result": "blocked"})
        return 2
    result = run_kr_day_close_service(config, runtime)
    _emit(result.model_dump(mode="json") | {"result": result.status})
    return 2 if result.status == "blocked" else 0


def _emit(payload: dict[str, str | bool | int | None]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
