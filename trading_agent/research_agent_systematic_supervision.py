from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from run_autonomous_research_cycle import REPORT_NAME
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

_SAMPLE_SECONDS: Final = 0.1
_GIB: Final = 1024**3


class SystematicChildProcess(Protocol):
    pid: int

    def wait(self, timeout: float | None = None) -> int: ...


type ProcessGroupRssReader = Callable[[int], int | None]


@dataclass(frozen=True, slots=True)
class SystematicChildSupervisorConfig:
    output: Path
    max_runtime_seconds: float
    rss_limit_gib: float

    @property
    def rss_limit_bytes(self) -> int:
        return int(self.rss_limit_gib * _GIB)


def reap_systematic_child(
    process: SystematicChildProcess,
    config: SystematicChildSupervisorConfig,
    rss_reader: ProcessGroupRssReader,
) -> None:
    reason: str | None = None
    deadline = time.monotonic() + config.max_runtime_seconds
    while reason is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = "systematic_child_timeout"
            break
        try:
            return_code = process.wait(timeout=min(_SAMPLE_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            rss_bytes = rss_reader(process.pid)
            if rss_bytes is None:
                reason = "systematic_child_rss_observation_failed"
            elif rss_bytes > config.rss_limit_bytes:
                reason = "systematic_child_rss_limit_exceeded"
            continue
        if return_code not in {0, 1}:
            reason = "systematic_child_failed"
        break
    if reason in {
        "systematic_child_timeout",
        "systematic_child_rss_observation_failed",
        "systematic_child_rss_limit_exceeded",
    }:
        _terminate_process_group(process)
    if reason is not None and not (config.output / REPORT_NAME).exists():
        with suppress(InvalidPrivateStableReportError):
            write_private_stable_report(config.output / REPORT_NAME, _blocked_child_report(reason))


def process_group_rss_bytes(process_group_id: int) -> int | None:
    try:
        completed = subprocess.run(
            ("/bin/ps", "-axo", "pgid=,rss="),
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
            env={"LANG": "C", "LC_ALL": "C", "PATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    total = 0
    for line in completed.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 2:
            return None
        try:
            group_id, rss_kib = (int(field) for field in fields)
        except ValueError:
            return None
        if group_id == process_group_id:
            total += rss_kib * 1024
    return total


def _terminate_process_group(process: SystematicChildProcess) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        _ = process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            _ = process.wait(timeout=5)


def _blocked_child_report(reason: str) -> str:
    return "\n".join(
        (
            "# Autonomous generated strategy research cycle",
            "",
            "- result: blocked",
            f"- {reason}",
            "- lifecycle authority: false",
            "- allocation authority: false",
            "- order authority: false",
            "- trading mutation: 0",
            "",
        )
    )


__all__ = (
    "SystematicChildProcess",
    "SystematicChildSupervisorConfig",
    "process_group_rss_bytes",
    "reap_systematic_child",
)
