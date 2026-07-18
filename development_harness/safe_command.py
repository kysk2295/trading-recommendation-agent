from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

_COMMAND_PATTERN = re.compile(r"^[A-Za-z0-9_./:=+@%,-]+(?: [A-Za-z0-9_./:=+@%,-]+)*$")
_PROTECTED_ROOTS = frozenset({".git", ".grok", ".hermes", ".omo"})
_SECRET_PATH_PARTS = frozenset({"credentials", "credential", "secrets", "secret", "id_rsa"})
_SECRET_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx")
_FORBIDDEN_COMMAND_MARKERS = (
    "://",
    "credential",
    "broker",
    "alpaca",
    "opendart",
    "run_kis",
    "run_alpaca",
    "run_ls",
    "run_opendart",
)
_PYTEST_FLAGS = frozenset(
    {"-q", "-qq", "-x", "--tb=no", "--tb=line", "--tb=short", "--no-header", "--disable-warnings"}
)

MAX_PATH_LENGTH: Final = 240
MAX_COMMAND_LENGTH: Final = 240
MAX_COMMAND_PARTS: Final = 32


def is_safe_path(value: str) -> bool:
    if not value or len(value) > MAX_PATH_LENGTH or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    for part in path.parts:
        lower = part.lower()
        if lower in _PROTECTED_ROOTS or lower in _SECRET_PATH_PARTS:
            return False
        if lower == ".env" or lower.startswith(".env."):
            return False
        if "token" in lower or "secret" in lower or lower.endswith(_SECRET_SUFFIXES):
            return False
    return True


def _is_repo_path_arg(value: str) -> bool:
    return is_safe_path(value) and not value.startswith("-")


def is_safe_command(value: str) -> bool:
    if not value or len(value) > MAX_COMMAND_LENGTH or not _COMMAND_PATTERN.fullmatch(value):
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in _FORBIDDEN_COMMAND_MARKERS):
        return False
    parts = value.split()
    if len(parts) < 3 or len(parts) > MAX_COMMAND_PARTS or parts[:2] != ["uv", "run"]:
        return False
    tool = parts[2]
    args = parts[3:]
    if tool == "pytest":
        if not args:
            return False
        saw_path = False
        for arg in args:
            if arg.startswith("-"):
                if arg in {"-p", "--plugins"} or arg.startswith("-p") or arg not in _PYTEST_FLAGS:
                    return False
                continue
            if not _is_repo_path_arg(arg) or not arg.startswith("tests/"):
                return False
            saw_path = True
        return saw_path
    if tool == "ruff":
        if not args or args[0] != "check":
            return False
        saw_path = False
        for arg in args[1:]:
            if arg == "--no-cache":
                continue
            if arg.startswith("-") or not _is_repo_path_arg(arg):
                return False
            saw_path = True
        return saw_path
    if tool == "basedpyright":
        return bool(args) and all(_is_repo_path_arg(arg) for arg in args)
    if tool == "python":
        return args == ["-c", "pass"] or args == ["run_grok_task.py", "--help"]
    return False
