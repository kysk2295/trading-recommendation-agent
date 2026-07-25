from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from trading_agent.daily_research_sources import load_session_quality
from trading_agent.dashboard_agents import agent_views
from trading_agent.dashboard_evidence import recommendations, research_view, signals
from trading_agent.dashboard_models import (
    DashboardCredentialError,
    DashboardCredentials,
    DashboardSnapshot,
    ForwardView,
    JobRow,
    MarketView,
)

SEOUL: Final = ZoneInfo("Asia/Seoul")
NEW_YORK: Final = ZoneInfo("America/New_York")
SESSION_DIRECTORY = re.compile(r"^\d{8}$")
MAX_CREDENTIAL_BYTES: Final = 4_096


def collect_dashboard_snapshot(
    outputs: Path,
    *,
    now: dt.datetime | None = None,
    jobs: Iterable[JobRow] = (),
) -> DashboardSnapshot:
    observed_at = dt.datetime.now(dt.UTC) if now is None else now
    if observed_at.tzinfo is None:
        raise ValueError("dashboard snapshot time must be timezone-aware")
    seoul_now = observed_at.astimezone(SEOUL)
    session = _latest_session(outputs / "live_sessions", seoul_now.date())
    return DashboardSnapshot(
        generated_at=observed_at,
        markets=(
            _market_view("kr", "한국", seoul_now),
            _market_view("us", "미국", observed_at.astimezone(NEW_YORK)),
        ),
        forward=_forward_view(session),
        agents=agent_views(jobs, seoul_now.date()),
        recommendations=recommendations(session),
        signals=signals(session),
        research=research_view(outputs),
    )


def load_dashboard_credentials(path: Path) -> DashboardCredentials:
    payload = _read_owner_file(path)
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError:
        raise DashboardCredentialError("invalid_settings") from None
    settings: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition("=")
        if not separator or not name or not value or name in settings:
            raise DashboardCredentialError("invalid_settings")
        settings[name] = value
    if set(settings) != {"DASHBOARD_URL", "DASHBOARD_INGEST_TOKEN"}:
        raise DashboardCredentialError("invalid_settings")
    url = settings["DASHBOARD_URL"].rstrip("/")
    parsed = urlsplit(url)
    is_local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (parsed.scheme != "https" and not is_local) or parsed.query or parsed.fragment:
        raise DashboardCredentialError("invalid_settings")
    token = settings["DASHBOARD_INGEST_TOKEN"]
    if len(token) < 24 or len(token) > 256 or any(character.isspace() for character in token):
        raise DashboardCredentialError("invalid_settings")
    return DashboardCredentials(url, SecretStr(token))


def _read_owner_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise DashboardCredentialError("credential_file_must_be_absolute_mode_600")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError:
        raise DashboardCredentialError("credential_file_must_be_absolute_mode_600") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise DashboardCredentialError("credential_file_must_be_absolute_mode_600")
        payload = os.read(descriptor, MAX_CREDENTIAL_BYTES + 1)
    except DashboardCredentialError:
        raise
    except OSError:
        raise DashboardCredentialError("credential_file_must_be_absolute_mode_600") from None
    finally:
        os.close(descriptor)
    if len(payload) > MAX_CREDENTIAL_BYTES:
        raise DashboardCredentialError("invalid_settings")
    return payload


def _latest_session(root: Path, today: dt.date) -> Path | None:
    if not root.is_dir():
        return None
    candidates: list[tuple[dt.date, Path]] = []
    for path in root.iterdir():
        if not path.is_dir() or SESSION_DIRECTORY.fullmatch(path.name) is None:
            continue
        session_date = dt.datetime.strptime(path.name, "%Y%m%d").date()
        if session_date <= today:
            candidates.append((session_date, path))
    return None if not candidates else max(candidates, key=lambda item: item[0])[1]


def _forward_view(session: Path | None) -> ForwardView:
    empty = ForwardView(
        session_date=None,
        eligible=False,
        ranking_cycles=0,
        watch_cycles=0,
        failed_watch_cycles=0,
        read_retries=0,
        read_retry_failures=0,
        candidate_input_cycles=0,
        candidate_inputs=0,
        recommendations=0,
        blockers=("session_unavailable",),
        incidents=(),
    )
    if session is None:
        return empty
    session_date = dt.datetime.strptime(session.name, "%Y%m%d").date()
    try:
        quality, incidents = load_session_quality(session, completed_trades=0)
    except (OSError, sqlite3.Error, ValueError):
        return empty.model_copy(
            update={"session_date": session_date, "blockers": ("session_unreadable",)}
        )
    blocking_prefixes = (
        "coverage_cycle_mismatch:",
        "ranking_request_count_mismatch:",
        "ranking_request_failures:",
        "watch_cycle_failures:",
        "retry_cycle_mismatch:",
        "kis_read_retry_failures:",
        "candidate_input_cycle_mismatch:",
        "candidate_input_incomplete_cycles:",
        "candidate_input_count_mismatch:",
    )
    return ForwardView(
        session_date=session_date,
        eligible=quality.forward_day_eligible,
        ranking_cycles=quality.ranking_cycles,
        watch_cycles=quality.watch_cycles,
        failed_watch_cycles=quality.failed_watch_cycles,
        read_retries=quality.read_retries,
        read_retry_failures=quality.read_retry_failures,
        candidate_input_cycles=quality.candidate_input_cycles,
        candidate_inputs=quality.candidate_inputs,
        recommendations=quality.recommendations,
        blockers=tuple(item for item in incidents if item.startswith(blocking_prefixes)),
        incidents=incidents,
    )


def _market_view(
    market_id: Literal["kr", "us"],
    label: str,
    local_time: dt.datetime,
) -> MarketView:
    if local_time.weekday() >= 5:
        state: Literal["open", "closed", "pre", "after"] = "closed"
    else:
        minutes = local_time.hour * 60 + local_time.minute
        opening, closing = ((540, 930) if market_id == "kr" else (570, 960))
        if minutes < opening:
            state = "pre"
        elif minutes < closing:
            state = "open"
        else:
            state = "after"
    return MarketView(market_id=market_id, label=label, local_time=local_time, state=state)


__all__ = (
    "DashboardCredentialError",
    "DashboardCredentials",
    "DashboardSnapshot",
    "JobRow",
    "collect_dashboard_snapshot",
    "load_dashboard_credentials",
)
