from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Callable
from pathlib import Path

PhaseAction = Callable[[], int]
Clock = Callable[[], dt.datetime]


def run_audited_kr_theme_day_post_session_phase(
    action: PhaseAction,
    audit_path: Path,
    clock: Clock,
) -> int:
    started_at = clock()
    try:
        exit_code = action()
    except (OSError, RuntimeError, ValueError):
        exit_code = 1
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        audit_path.parent.chmod(0o700)
        has_header = audit_path.is_file() and audit_path.stat().st_size > 0
        with audit_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if not has_header:
                writer.writerow(("started_at", "exit_code", "status"))
            writer.writerow(
                (
                    started_at.isoformat(),
                    exit_code,
                    "ok" if exit_code == 0 else "failed",
                )
            )
        audit_path.chmod(0o600)
    except OSError:
        return 1
    return exit_code


def kr_theme_day_post_session_phase_status(exit_code: int | None) -> str:
    if exit_code is None:
        return "not_started"
    return "success" if exit_code == 0 else "failed"


__all__ = (
    "kr_theme_day_post_session_phase_status",
    "run_audited_kr_theme_day_post_session_phase",
)
