#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Install uv.
# 2. Run: uv run --script run_research_agent_source_supply.py status
# 3. Materialize and inspect: uv run --script run_research_agent_source_supply.py tick
# ─────────────────

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    load_research_agent_service_config,
)
from trading_agent.research_agent_source_common import InvalidResearchAgentSourceError
from trading_agent.research_agent_source_supply import InvalidMarketContextSupplyError
from trading_agent.research_agent_source_supply_status import (
    canonical_source_supply_json,
    inspect_source_supply,
)
from trading_agent.research_agent_systematic_input_store import InvalidSystematicInputActivationError

Clock = Callable[[], dt.datetime]
_DEFAULT_CONFIG = Path.home() / ".config" / "trading-agent" / "research-agent-runtime-v2.json"
_INVALID = (
    '{"allocation_mutation":0,"broker_mutation":0,"heavy_processes":0,'
    '"model_calls":0,"network_calls":0,"order_authority_mutation":0,'
    '"provider_calls":0,"reason":"source_supply_input_invalid","state":"blocked"}'
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Path-free six-family local source availability and supply")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "tick"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
        command.add_argument("--now", type=_aware_datetime)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    try:
        args = parse_args(argv)
        now = clock() if args.now is None else args.now
        config = load_research_agent_service_config(args.config)
        report = inspect_source_supply(config, now, args.command == "tick")
    except (
        InvalidMarketContextSupplyError,
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        InvalidResearchAgentSourceError,
        InvalidSystematicInputActivationError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        print(_INVALID, file=sys.stderr)
        return 2
    print(canonical_source_supply_json(report))
    return 1 if report.state == "blocked" else 0


def _aware_datetime(raw: str) -> dt.datetime:
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("now must be ISO-8601") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise argparse.ArgumentTypeError("now must include a UTC offset")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
