#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Prepare actual evidence: uv run run_research_agent_soak.py prepare --database /private/path/soak.sqlite3
# 2. Add a checkpoint:
#      uv run run_research_agent_soak.py checkpoint --database /private/path/soak.sqlite3 --kind heartbeat
# 3. Query status: uv run run_research_agent_soak.py status --database /private/path/soak.sqlite3
# ─────────────────

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never

from trading_agent.research_agent_soak_models import (
    SoakCheckpointKind,
    SoakEvidenceMode,
    SoakState,
    canonical_status_json,
)
from trading_agent.research_agent_soak_runtime import (
    InvalidSoakRuntimeIdentityError,
    capture_soak_observation,
    current_utc_time,
)
from trading_agent.research_agent_soak_status import build_research_agent_soak_status
from trading_agent.research_agent_soak_store import (
    InvalidResearchAgentSoakStoreError,
    ResearchAgentSoakStore,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and inspect durable research-agent soak evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="create a new append-only soak database")
    prepare.add_argument("--database", required=True, type=Path)
    prepare.add_argument("--mode", choices=tuple(SoakEvidenceMode), default=SoakEvidenceMode.ACTUAL)
    checkpoint = commands.add_parser("checkpoint", help="append one internally timestamped checkpoint")
    checkpoint.add_argument("--database", required=True, type=Path)
    checkpoint.add_argument(
        "--kind",
        required=True,
        choices=tuple(kind for kind in SoakCheckpointKind if kind is not SoakCheckpointKind.PREPARED),
    )
    status = commands.add_parser("status", help="verify the full chain and report readiness")
    status.add_argument("--database", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        store = ResearchAgentSoakStore(args.database)
        match args.command:
            case "prepare":
                mode = SoakEvidenceMode(args.mode)
                match mode:
                    case SoakEvidenceMode.ACTUAL:
                        _ = store.prepare_actual()
                    case SoakEvidenceMode.CONTROLLED_FIXTURE:
                        _ = store.prepare_controlled(capture_soak_observation())
                    case unreachable:
                        assert_never(unreachable)
            case "checkpoint":
                _ = store.append_current(SoakCheckpointKind(args.kind))
            case "status":
                pass
            case unreachable:
                assert_never(unreachable)
        status = build_research_agent_soak_status(store.records(), current_utc_time())
    except (InvalidResearchAgentSoakStoreError, InvalidSoakRuntimeIdentityError):
        print("research-agent soak evidence is invalid", file=sys.stderr)
        return 2
    print(canonical_status_json(status))
    return 1 if status.status is SoakState.EXPIRED else 0


if __name__ == "__main__":
    raise SystemExit(main())
