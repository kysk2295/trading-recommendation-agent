from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import anyio

from trading_agent.dashboard_autonomous_publisher import (
    autonomous_trigger_paths,
    stream_autonomous_trigger_event,
)
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_native_watch import watch_native_changes
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2

MAX_RECONNECT_SECONDS = 60
WATCH_DEBOUNCE_MS = 2_000
WATCH_STEP_MS = 250


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
) -> None:
    event_source = watch_native_changes if watcher is None else watcher
    async for changes in event_source(
        *watch_roots(outputs),
        debounce=WATCH_DEBOUNCE_MS,
        step=WATCH_STEP_MS,
    ):
        for trigger_path in autonomous_trigger_paths(changes):
            await stream_autonomous_trigger_event(
                socket,
                trigger_path,
                send_lock,
            )
        snapshot = collect_dashboard_snapshot_v2(outputs)
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
        root / "derivatives",
        root / "paper",
        root / "system",
    )
    existing = tuple(path for path in candidates if path.is_dir())
    return existing or (root,)


def reconnect_delay_seconds(attempt: int) -> int:
    return min(MAX_RECONNECT_SECONDS, 5 * 2 ** max(0, attempt))


__all__ = (
    "SnapshotSocket",
    "WatchFactory",
    "publisher_url",
    "reconnect_delay_seconds",
    "send_snapshot",
    "watch_output_events",
    "watch_roots",
)
