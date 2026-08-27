#!/usr/bin/env -S uv run --offline --script
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from trading_agent.kr_loop_active_release import InvalidKrLoopActiveReleaseError, resolve_active_source
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore

Executor = Callable[[tuple[str, ...], dict[str, str]], None]


def main(argv: Sequence[str] | None = None, *, executor: Executor | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    try:
        source = resolve_active_source(
            args.active_release,
            args.repository.expanduser().absolute(),
            KrLoopReleaseArtifactStore(args.artifact_root),
        )
        script = source / "run_research_agent_runtime.py"
        if not script.is_file() or script.is_symlink():
            raise InvalidKrLoopActiveReleaseError
        command = (sys.executable, str(script), "run", "--config", str(args.config.expanduser().absolute()))
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(source) if not existing else str(source) + os.pathsep + existing
        active = _exec if executor is None else executor
        active(command, environment)
        return 0
    except (InvalidKrLoopActiveReleaseError, OSError, TypeError, ValueError):
        print("invalid active Research Agent runtime request", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the verified active paper-only Research Agent release")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--active-release", type=Path, required=True)
    run.add_argument("--repository", type=Path, required=True)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    return parser


def _exec(command: tuple[str, ...], environment: dict[str, str]) -> NoReturn:
    os.execve(command[0], command, environment)


if __name__ == "__main__":
    raise SystemExit(main())
