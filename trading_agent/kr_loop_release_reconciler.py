from __future__ import annotations

import datetime as dt
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.kr_loop_active_release import (
    InvalidKrLoopActiveReleaseError,
    KrLoopActiveRelease,
    active_release_for_event,
    baseline_active_release,
    load_active_release,
    replace_active_release,
)
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore
from trading_agent.research_agent_service_config import RESEARCH_AGENT_SERVICE_LABEL

LaunchctlRunner = Callable[[tuple[str, ...]], int]


class InvalidKrLoopReleaseReconciliationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop release reconciliation failed"


@dataclass(frozen=True, slots=True)
class KrLoopReleaseReconciliation:
    changed: bool
    restarted: bool
    active: KrLoopActiveRelease


def reconcile_active_release(
    *,
    store: KrLoopEngineerStore,
    artifacts: KrLoopReleaseArtifactStore,
    repository: Path,
    active_path: Path,
    now: dt.datetime,
    runner: LaunchctlRunner | None = None,
) -> KrLoopReleaseReconciliation:
    try:
        releases = store.releases()
        if not releases:
            raise InvalidKrLoopReleaseReconciliationError
        event = releases[-1]
        desired = active_release_for_event(repository, artifacts, event, now)
        try:
            current = load_active_release(active_path)
        except InvalidKrLoopActiveReleaseError:
            current = None
        if current is not None and _same_deployment(current, desired):
            return KrLoopReleaseReconciliation(False, False, current)
        fallback = current or baseline_active_release(artifacts, event, now)
        _ = replace_active_release(active_path, desired)
        active_runner = _run_launchctl if runner is None else runner
        target = f"gui/{os.getuid()}/{RESEARCH_AGENT_SERVICE_LABEL}"
        if active_runner(("/bin/launchctl", "kickstart", "-k", target)) != 0:
            _ = replace_active_release(active_path, fallback)
            raise InvalidKrLoopReleaseReconciliationError
        return KrLoopReleaseReconciliation(True, True, desired)
    except (
        InvalidKrLoopActiveReleaseError,
        InvalidKrLoopReleaseReconciliationError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrLoopReleaseReconciliationError from None


def _same_deployment(current: KrLoopActiveRelease, desired: KrLoopActiveRelease) -> bool:
    return (
        current.generation == desired.generation
        and current.release_id == desired.release_id
        and current.candidate_id == desired.candidate_id
        and current.action == desired.action
        and current.source_root == desired.source_root
        and current.active_commit == desired.active_commit
    )


def _run_launchctl(command: tuple[str, ...]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


__all__ = (
    "InvalidKrLoopReleaseReconciliationError",
    "KrLoopReleaseReconciliation",
    "LaunchctlRunner",
    "reconcile_active_release",
)
