from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_VERIFICATION_TIMEOUT_SECONDS: Final = 1_800
_CACHE_DISABLE_ENV: Final[Mapping[str, str]] = {
    "PYTHONDONTWRITEBYTECODE": "1",
}
_PYTEST_ADDOPTS_EXACT: Final = "-p no:cacheprovider"
_RUFF_NO_CACHE_FLAG: Final = "--no-cache"


class GrokVerificationError(RuntimeError):
    """Raised when offline verification cannot run safely."""


def cache_disabled_environ(*, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a process environment with Python/pytest tool caches disabled.

    Shared by the Grok worker process and independent offline verification so
    neither path can drift into writing pytest or bytecode cache artifacts that
    would mutate ignored-path snapshot metadata.

    ``PYTEST_ADDOPTS`` is always set to exactly ``-p no:cacheprovider``.
    Inherited pytest options are discarded fail-closed so a caller cannot
    re-enable cacheprovider or load plugins after the injected pair. Unrelated
    environment keys are preserved. Ruff cache is disabled by injecting the
    documented ``--no-cache`` flag into commands, not via env.
    """

    env = dict(os.environ if base is None else base)
    env.update(_CACHE_DISABLE_ENV)
    env["PYTEST_ADDOPTS"] = _PYTEST_ADDOPTS_EXACT
    return env


def _inject_ruff_no_cache(parts: list[str]) -> list[str]:
    """Insert documented ``ruff check --no-cache`` when missing (idempotent)."""

    try:
        ruff_at = parts.index("ruff")
    except ValueError:
        return parts
    if ruff_at + 1 >= len(parts) or parts[ruff_at + 1] != "check":
        return parts
    if _RUFF_NO_CACHE_FLAG in parts[ruff_at + 2 :]:
        return parts
    return [*parts[: ruff_at + 2], _RUFF_NO_CACHE_FLAG, *parts[ruff_at + 2 :]]


def cache_safe_command(command: str) -> str:
    """Return the worker-facing command string with Ruff ``--no-cache`` injected."""

    parts = command.split()
    if len(parts) < 3 or parts[0] != "uv" or parts[1] != "run":
        raise GrokVerificationError("invalid verification command")
    return " ".join(_inject_ruff_no_cache(parts))


def offline_command(command: str) -> tuple[str, ...]:
    parts = command.split()
    if len(parts) < 3 or parts[0] != "uv" or parts[1] != "run":
        raise GrokVerificationError("invalid verification command")
    with_offline = parts if parts[2] == "--offline" else ["uv", "run", "--offline", *parts[2:]]
    return tuple(_inject_ruff_no_cache(with_offline))


def run_verification_command(command: tuple[str, ...], *, cwd: Path) -> int:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_VERIFICATION_TIMEOUT_SECONDS,
        env=cache_disabled_environ(),
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
