from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_plan_compiler import (
    compile_future_session_plan,
)
from trading_agent.future_session_plan_models import (
    FutureSessionPlanRequest,
    canonical_plan_json,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        request_path = Path(arguments.request)
        if not request_path.is_absolute():
            raise ValueError
        request = FutureSessionPlanRequest.model_validate_json(
            _read_request(request_path)
        )
        decision = compile_future_session_plan(request)
    except (OSError, TypeError, ValidationError, ValueError):
        sys.stdout.write(
            json.dumps(
                {"result": "invalid_request"},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return 2
    sys.stdout.write(canonical_plan_json(decision))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile a read-only provenance-bound future-session plan."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser(
        "compile",
        help="Compile and report one typed plan decision.",
    )
    compile_parser.add_argument(
        "--request",
        required=True,
        help="Absolute path to a mode-600 canonical request JSON file.",
    )
    return parser


def _read_request(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ValueError
        payload = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise ValueError
        return bytes(payload)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
