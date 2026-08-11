from __future__ import annotations

import datetime as dt
import json
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

import anyio

from trading_agent.dashboard_autonomous_publisher import (
    DEFAULT_AUTONOMOUS_STATE,
    autonomous_trigger_paths,
    stream_autonomous_trigger_event,
)
from trading_agent.dashboard_kr_autonomous_bridge import (
    InvalidKrAutonomousBridgeError,
    publish_kr_autonomous_triggers,
)
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_native_watch import watch_native_changes
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifierInput,
)

MAX_RECONNECT_SECONDS = 60
WATCH_DEBOUNCE_MS = 2_000
WATCH_STEP_MS = 250
_MAIN_BRANCH: Final = "main"


class DashboardPublisherAuthorityBlocker(StrEnum):
    BRANCH_INVALID = "dashboard_publisher_authority_branch_invalid"
    CHECK_UNAVAILABLE = "dashboard_publisher_authority_check_unavailable"
    HEAD_NOT_CURRENT = "dashboard_publisher_authority_head_not_current"
    REF_UNAVAILABLE = "dashboard_publisher_authority_ref_unavailable"
    TRACKED_TREE_DIRTY = "dashboard_publisher_authority_tracked_tree_dirty"


@dataclass(frozen=True, slots=True)
class DashboardPublisherAuthorityError(Exception):
    blocker: DashboardPublisherAuthorityBlocker

    def __str__(self) -> str:
        return self.blocker


class SnapshotSocket(Protocol):
    async def send(self, message: str) -> None: ...


class WatchFactory(Protocol):
    def __call__(
        self,
        *paths: Path,
        debounce: int,
        step: int,
    ) -> AsyncIterator[frozenset[Path]]: ...


async def send_snapshot(socket: SnapshotSocket, snapshot: DashboardSnapshotV2) -> None:
    await socket.send(
        json.dumps(
            {"type": "snapshot", "snapshot": snapshot.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


async def watch_output_events(
    socket: SnapshotSocket,
    outputs: Path,
    send_lock: anyio.Lock,
    watcher: WatchFactory | None = None,
    system_authority_verifier: SystemAuthorityVerifierInput = None,
    cycle_database: Path | None = None,
) -> None:
    event_source = watch_native_changes if watcher is None else watcher
    code_sha = current_code_sha()
    async for changes in event_source(
        *watch_roots(outputs),
        debounce=WATCH_DEBOUNCE_MS,
        step=WATCH_STEP_MS,
    ):
        try:
            generated = publish_kr_autonomous_triggers(
                outputs,
                state_root=DEFAULT_AUTONOMOUS_STATE,
                pinned_code_sha=code_sha,
                now=dt.datetime.now(dt.UTC),
            )
        except InvalidKrAutonomousBridgeError:
            generated = ()
        for trigger_path in tuple(sorted(set(autonomous_trigger_paths(changes)) | set(generated))):
            await stream_autonomous_trigger_event(
                socket,
                trigger_path,
                send_lock,
            )
        if cycle_database is None:
            snapshot = collect_dashboard_snapshot_v2(
                outputs,
                system_authority_verifier=system_authority_verifier,
            )
        else:
            snapshot = collect_dashboard_snapshot_v2(
                outputs,
                system_authority_verifier=system_authority_verifier,
                cycle_database=cycle_database,
            )
        async with send_lock:
            await send_snapshot(socket, snapshot)


def publisher_url(dashboard_url: str) -> str:
    parsed = urlsplit(dashboard_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/api/realtime/publish", "", ""))


def watch_roots(outputs: Path) -> tuple[Path, ...]:
    root = outputs.resolve()
    candidates = (
        root / "live_sessions",
        root / "source_evidence",
        root / "experiment_control",
        root / "lane_control",
        root / "kr_theme",
        root / "derivatives",
        root / "paper",
        root / "hermes",
        root / "system",
    )
    existing = tuple(path for path in candidates if path.is_dir())
    return existing or (root,)


def reconnect_delay_seconds(attempt: int) -> int:
    return min(MAX_RECONNECT_SECONDS, 5 * 2 ** max(0, attempt))


def current_code_sha() -> str:
    completed = subprocess.run(
        ("git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def require_current_main_authority() -> None:
    repository = Path(__file__).resolve().parents[1]
    branch = _git_authority_optional(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != _MAIN_BRANCH:
        raise DashboardPublisherAuthorityError(DashboardPublisherAuthorityBlocker.BRANCH_INVALID)
    tracked_changes = _git_authority_value(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        blocker=DashboardPublisherAuthorityBlocker.CHECK_UNAVAILABLE,
    )
    if tracked_changes:
        raise DashboardPublisherAuthorityError(DashboardPublisherAuthorityBlocker.TRACKED_TREE_DIRTY)
    head = _git_authority_value(
        repository,
        "rev-parse",
        "HEAD",
        blocker=DashboardPublisherAuthorityBlocker.CHECK_UNAVAILABLE,
    )
    local_main = _git_authority_value(
        repository,
        "rev-parse",
        "refs/heads/main",
        blocker=DashboardPublisherAuthorityBlocker.REF_UNAVAILABLE,
    )
    origin_main = _git_authority_value(
        repository,
        "rev-parse",
        "refs/remotes/origin/main",
        blocker=DashboardPublisherAuthorityBlocker.REF_UNAVAILABLE,
    )
    if head != local_main or head != origin_main:
        raise DashboardPublisherAuthorityError(DashboardPublisherAuthorityBlocker.HEAD_NOT_CURRENT)


def _git_authority_optional(repository: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise DashboardPublisherAuthorityError(DashboardPublisherAuthorityBlocker.CHECK_UNAVAILABLE) from None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git_authority_value(
    repository: Path,
    *arguments: str,
    blocker: DashboardPublisherAuthorityBlocker,
) -> str:
    value = _git_authority_optional(repository, *arguments)
    if value is None:
        raise DashboardPublisherAuthorityError(blocker)
    return value


__all__ = (
    "DashboardPublisherAuthorityBlocker",
    "DashboardPublisherAuthorityError",
    "SnapshotSocket",
    "WatchFactory",
    "current_code_sha",
    "publisher_url",
    "reconnect_delay_seconds",
    "require_current_main_authority",
    "send_snapshot",
    "watch_output_events",
    "watch_roots",
)
