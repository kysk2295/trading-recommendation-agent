from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from trading_agent.day_session_service_config import (
    DaySessionServiceConfig,
    KrDaySessionServiceConfig,
    UsDaySessionServiceConfig,
)
from trading_agent.us_day_session_tick import UsDaySessionTickRequest, run_us_day_session_tick
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_KST = ZoneInfo("Asia/Seoul")
Clock = Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class DaySessionServiceResult:
    market: Literal["us", "kr"]
    status: Literal["processed", "no_action"]
    reason: str
    mutation: Literal[0] = 0


def run_day_session_service_tick(
    config: DaySessionServiceConfig,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> DaySessionServiceResult:
    now = clock().astimezone(dt.UTC)
    if _session_is_closed(config, now):
        return DaySessionServiceResult(config.market, "no_action", "session_closed")
    authority = _authority_reason(config)
    if authority is not None:
        return DaySessionServiceResult(config.market, "no_action", authority)
    match config:
        case UsDaySessionServiceConfig():
            code, reason = _run_us(config, now)
        case KrDaySessionServiceConfig():
            code, reason = _run_kr(config)
    return DaySessionServiceResult(
        config.market,
        "processed" if code == 0 else "no_action",
        reason,
    )


def _session_is_closed(config: DaySessionServiceConfig, now: dt.datetime) -> bool:
    match config:
        case UsDaySessionServiceConfig():
            bounds = regular_session_bounds(now.astimezone(NEW_YORK).date())
            return bounds is None or not bounds[0] <= now < bounds[1]
        case KrDaySessionServiceConfig():
            local = now.astimezone(_KST)
            return local.weekday() >= 5 or not dt.time(9) <= local.time() < dt.time(15, 30)


def _authority_reason(config: DaySessionServiceConfig) -> str | None:
    try:
        root = config.project_root.resolve(strict=True)
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = _git(root, "rev-parse", "HEAD")
        local_main = _git(root, "rev-parse", "refs/heads/main")
        tracked = _git(root, "status", "--porcelain=v1", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "project_root_invalid"
    if root != config.project_root or branch != "main" or head != local_main or head != config.expected_commit:
        return "commit_mismatch"
    return "tracked_worktree_dirty" if tracked else None


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _run_us(config: UsDaySessionServiceConfig, now: dt.datetime) -> tuple[int, str]:
    fixed = tuple(config.source_root / name for name in (
        "scanner.json",
        "articles.json",
        "news-evidence.json",
        "market-context.json",
    ))
    quotes = tuple(sorted((config.source_root / "quotes").glob("*.json")))
    ticks = tuple(sorted((config.source_root / "completed-ticks").glob("*.json")))
    if any(not path.is_file() for path in fixed) or not quotes or not ticks:
        return 2, "source_missing"
    code, result = run_us_day_session_tick(
        UsDaySessionTickRequest(
            scanner=fixed[0],
            articles=fixed[1],
            news_evidence=fixed[2],
            market_context=fixed[3],
            quotes=quotes,
            completed_ticks=ticks,
            outputs=config.state_root,
            evaluated_at=now,
            version_store=config.source_root / "version-store.sqlite3",
            production_manifest=config.source_root / "production-manifest.json",
            live_model_provider=config.live_model_provider,
        )
    )
    return code, result.reason or result.tick_status or result.status


def _run_kr(config: KrDaySessionServiceConfig) -> tuple[int, str]:
    requests = tuple(sorted(config.source_root.glob("*.json")))
    if not requests:
        return 2, "source_missing"
    command = (
        sys.executable,
        str(config.project_root / "run_kr_day_capsule_shadow.py"),
        *(value for path in requests[-3:] for value in ("--request", str(path))),
        "--store",
        str(config.state_root / "kr-day-capsule-shadow.sqlite3"),
        "--output",
        str(config.state_root / "receipts"),
    )
    completed = _run_child(command)
    try:
        payload = json.loads(completed.stdout)
        reason = str(payload.get("result", "kr_capsule_blocked"))
    except (json.JSONDecodeError, AttributeError):
        reason = "kr_capsule_child_invalid"
    return completed.returncode, reason


def _run_child(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 2, "", "")


__all__ = ("DaySessionServiceResult", "run_day_session_service_tick")
