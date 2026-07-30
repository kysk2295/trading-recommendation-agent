from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.future_session_us_materializer import (
    FutureSessionMaterializationError,
    materialize_us_future_session,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        manifest = materialize_us_future_session(
            request_path=Path(arguments.request),
            plan_path=Path(arguments.plan),
            output_dir=Path(arguments.output_dir),
        )
    except (FutureSessionMaterializationError, OSError, TypeError, ValueError):
        sys.stdout.write(
            json.dumps(
                {"result": "invalid_materialization_authority"},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(
            {"manifest": str(manifest), "result": "prepared"},
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare five provenance-bound US session jobs without submitting them."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Atomically prepare local artifacts.")
    prepare.add_argument("--request", required=True)
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output-dir", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
