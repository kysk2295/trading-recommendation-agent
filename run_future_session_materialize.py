from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.future_session_us_activation import (
    FutureSessionActivationError,
    activate_us_future_session,
)
from trading_agent.future_session_us_materializer import (
    FutureSessionMaterializationError,
    materialize_us_future_session,
)
from trading_agent.future_session_us_materializer_models import (
    UsFutureSessionMaterializationRequest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "prepare":
        return _prepare(arguments)
    return _activate(arguments)


def _prepare(arguments: argparse.Namespace) -> int:
    try:
        manifest = materialize_us_future_session(
            UsFutureSessionMaterializationRequest(
                request_path=Path(arguments.request),
                plan_path=Path(arguments.plan),
                output_dir=Path(arguments.output_dir),
            )
        )
    except (FutureSessionMaterializationError, OSError, TypeError, ValueError):
        _write({"result": "invalid_materialization_authority"})
        return 2
    _write({"manifest": str(manifest), "result": "prepared"})
    return 0


def _activate(arguments: argparse.Namespace) -> int:
    try:
        activation = activate_us_future_session(
            manifest_path=Path(arguments.manifest),
        )
    except FutureSessionActivationError as error:
        _write({"reason": error.reason, "result": "blocked"})
        return 2
    except (OSError, TypeError, ValueError):
        _write({"reason": "artifact_io_failed", "result": "blocked"})
        return 2
    _write(
        {
            "labels": [entry.label for entry in activation.entries],
            "receipt": str(activation.receipt_path),
            "result": "activated",
        }
    )
    return 0


def _write(payload: dict[str, str | list[str]]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or activate five provenance-bound US session jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Atomically prepare local artifacts.")
    prepare.add_argument("--request", required=True)
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output-dir", required=True)
    activate = commands.add_parser("activate", help="Install and bootstrap prepared jobs.")
    activate.add_argument("--manifest", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
