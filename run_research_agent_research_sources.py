#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly:
#      uv run run_research_agent_research_sources.py inspect --config <path> --now <ISO-8601>
# 3. Or make executable and run:
#      chmod +x run_research_agent_research_sources.py && ./run_research_agent_research_sources.py --help
# ─────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.research_agent_research_source_inspection import (
    inspect_research_sources,
    load_research_inspection_source_paths,
)
from trading_agent.research_agent_source_common import InvalidResearchAgentSourceError


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only readiness for exactly three Research source families.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect",
        help="Inspect local sources with required --config and deterministic --now.",
    )
    inspect.add_argument("--config", type=Path, required=True)
    inspect.add_argument("--now", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        now = dt.datetime.fromisoformat(args.now)
        if now.tzinfo is None or now.utcoffset() is None:
            raise InvalidResearchAgentSourceError(reason="collection_time_invalid")
        paths = load_research_inspection_source_paths(args.config)
        inspection = inspect_research_sources(paths, now)
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentSourceError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        print(
            '{"broker_mutation":0,"heavy_processes":0,"model_calls":0,"provider_calls":0,"status":"invalid"}',
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            inspection.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
