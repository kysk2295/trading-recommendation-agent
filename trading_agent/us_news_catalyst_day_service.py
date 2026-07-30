from __future__ import annotations

import datetime as dt
import fcntl
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_news_catalyst_day_service_config import (
    InvalidUsNewsCatalystDayServiceError,
    UsNewsCatalystDayServiceConfig,
)
from trading_agent.us_news_catalyst_day_session_manifest import (
    UsNewsCatalystDaySessionManifest,
    UsNewsCatalystDaySessionPaths,
    load_us_news_catalyst_day_session_manifest,
)
from trading_agent.us_news_catalyst_research_registration import (
    load_us_news_catalyst_research_manifest,
)

_SESSION_SCRIPT: Final = "run_us_news_catalyst_day_session.py"
CommandRunner = Callable[[tuple[str, ...]], int]


class UsNewsCatalystDayServiceLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day service lease is unavailable"


class UsNewsCatalystDayServiceStatus(StrEnum):
    WAITING = "waiting"
    INITIALIZED = "initialized"
    TICKED = "ticked"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDayServiceRuntime:
    runner: CommandRunner


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDayServiceTickResult:
    status: UsNewsCatalystDayServiceStatus
    session_date: dt.date
    manifest_path: Path | None
    reason_code: str | None


def run_us_news_catalyst_day_service_tick(
    config: UsNewsCatalystDayServiceConfig,
    observed_at: dt.datetime,
    runtime: UsNewsCatalystDayServiceRuntime,
) -> UsNewsCatalystDayServiceTickResult:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise InvalidUsNewsCatalystDayServiceError
    with _service_lease(config.session_root):
        return _run_tick_locked(config, observed_at, runtime)


def _run_tick_locked(
    config: UsNewsCatalystDayServiceConfig,
    observed_at: dt.datetime,
    runtime: UsNewsCatalystDayServiceRuntime,
) -> UsNewsCatalystDayServiceTickResult:
    local = observed_at.astimezone(NEW_YORK)
    session_date = local.date()
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        return UsNewsCatalystDayServiceTickResult(
            UsNewsCatalystDayServiceStatus.WAITING, session_date, None, "non_session_day"
        )
    session_root = config.session_root / session_date.isoformat()
    manifest_path = session_root / "session.json"
    created = False
    if not manifest_path.exists():
        if observed_at >= bounds[0]:
            return UsNewsCatalystDayServiceTickResult(
                UsNewsCatalystDayServiceStatus.BLOCKED,
                session_date,
                manifest_path,
                "bootstrap_window_missed",
            )
        if runtime.runner(_init_command(config, session_date, session_root, manifest_path)) != 0:
            return UsNewsCatalystDayServiceTickResult(
                UsNewsCatalystDayServiceStatus.BLOCKED, session_date, manifest_path, "init_failed"
            )
        created = True
    manifest = load_us_news_catalyst_day_session_manifest(manifest_path)
    _require_binding(config, session_root, manifest)
    if runtime.runner(_tick_command(config, manifest_path, session_root)) != 0:
        return UsNewsCatalystDayServiceTickResult(
            UsNewsCatalystDayServiceStatus.BLOCKED, session_date, manifest_path, "tick_failed"
        )
    status = UsNewsCatalystDayServiceStatus.INITIALIZED if created else UsNewsCatalystDayServiceStatus.TICKED
    return UsNewsCatalystDayServiceTickResult(status, session_date, manifest_path, None)


def _init_command(
    config: UsNewsCatalystDayServiceConfig,
    session_date: dt.date,
    session_root: Path,
    manifest_path: Path,
) -> tuple[str, ...]:
    return (
        str(config.uv_path),
        "run",
        "--offline",
        "python",
        _SESSION_SCRIPT,
        "init",
        "--registration-manifest",
        str(config.registration_manifest),
        "--session-date",
        session_date.isoformat(),
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--projection-root",
        str(config.projection_root),
        "--evidence-root",
        str(config.evidence_root),
        "--security-master-store",
        str(config.security_master_store),
        "--session-root",
        str(session_root),
        "--manifest",
        str(manifest_path),
        "--secret-path",
        str(config.secret_path),
        "--output-dir",
        str(session_root / "service-init-report"),
    )


def _tick_command(
    config: UsNewsCatalystDayServiceConfig,
    manifest_path: Path,
    session_root: Path,
) -> tuple[str, ...]:
    return (
        str(config.uv_path),
        "run",
        "--offline",
        "python",
        _SESSION_SCRIPT,
        "tick",
        "--manifest",
        str(manifest_path),
        "--output-dir",
        str(session_root / "service-tick-report"),
    )


def _require_binding(
    config: UsNewsCatalystDayServiceConfig,
    session_root: Path,
    manifest: UsNewsCatalystDaySessionManifest,
) -> None:
    registration = load_us_news_catalyst_research_manifest(config.registration_manifest)
    expected = UsNewsCatalystDaySessionPaths(
        experiment_ledger=config.experiment_ledger,
        registration_manifest=config.registration_manifest,
        projection_root=config.projection_root,
        evidence_root=config.evidence_root,
        security_master_store=config.security_master_store,
        artifact_root=session_root / "artifacts",
        plan_root=session_root / "plans",
        profile_root=session_root / "profiles",
        runtime_root=session_root / "runtime",
        canonical_root=session_root / "canonical",
        feature_root=session_root / "features",
        receipt_root=session_root / "receipts",
        review_root=session_root / "reviews",
        audit_store=session_root / "audit.sqlite3",
        output_root=session_root / "phase-reports",
        secret_path=config.secret_path,
    )
    if (
        manifest.strategy_version != registration.strategy_version
        or manifest.code_version != registration.code_version
        or manifest.paths != expected
    ):
        raise InvalidUsNewsCatalystDayServiceError


@contextmanager
def _service_lease(root: Path) -> Iterator[None]:
    directory = open_private_parent(root, create=True)
    descriptor = -1
    try:
        require_private_directory(directory)
        descriptor = os.open(".day-service.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise InvalidUsNewsCatalystDayServiceError
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UsNewsCatalystDayServiceLeaseUnavailableError from error
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


__all__ = (
    "UsNewsCatalystDayServiceLeaseUnavailableError",
    "UsNewsCatalystDayServiceRuntime",
    "UsNewsCatalystDayServiceStatus",
    "run_us_news_catalyst_day_service_tick",
)
