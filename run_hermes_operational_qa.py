#!/usr/bin/env -S uv run --offline --script

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path

from trading_agent.hermes_operational_qa import (
    HermesOperationalQaRequest,
    InvalidHermesOperationalQaError,
    run_hermes_operational_qa,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run offline Hermes operational QA fixtures and sanitized reconciliation."
    )
    parser.add_argument("--delivery-store", type=Path)
    parser.add_argument("--execution-store", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("outputs"))
    parser.add_argument("--observed-at", type=_datetime, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _ = run_hermes_operational_qa(
            HermesOperationalQaRequest(
                delivery_store=args.delivery_store,
                execution_store=args.execution_store,
                output_root=args.output_root,
                observed_at=args.observed_at,
            )
        )
    except InvalidHermesOperationalQaError:
        print(json.dumps({"reason": "invalid_operational_qa_input", "result": "blocked"}, separators=(",", ":")))
        return 2
    print(json.dumps({"result": "recorded"}, separators=(",", ":")))
    return 0


def _datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
