from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

_VERIFICATION_TIMEOUT_SECONDS: Final = 1_800


class GrokVerificationError(RuntimeError):
    """Raised when offline verification cannot run safely."""


def offline_command(command: str) -> tuple[str, ...]:
    parts = command.split()
    if len(parts) < 3 or parts[0] != "uv" or parts[1] != "run":
        raise GrokVerificationError("invalid verification command")
    if parts[2] == "--offline":
        return tuple(parts)
    return ("uv", "run", "--offline", *parts[2:])


def run_verification_command(command: tuple[str, ...], *, cwd: Path) -> int:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_VERIFICATION_TIMEOUT_SECONDS,
    )
    return completed.returncode


def run_contract_commands(commands: tuple[str, ...], *, cwd: Path) -> bool:
    try:
        for command in commands:
            if run_verification_command(offline_command(command), cwd=cwd) != 0:
                return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True
