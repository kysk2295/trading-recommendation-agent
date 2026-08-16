from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_research_agent_systematic import _config
from trading_agent.research_agent_systematic_supervision import (
    SystematicChildSupervisorConfig,
    reap_systematic_child,
)


def test_detached_systematic_child_stops_when_process_group_exceeds_rss_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OverLimitProcess:
        __slots__ = ("pid", "wait_calls")

        def __init__(self) -> None:
            self.pid = 4242
            self.wait_calls = 0

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("systematic-child", 0.0 if timeout is None else timeout)
            return -15

    terminated: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda process_id, signal_number: terminated.append((process_id, signal_number)))
    config = _config(tmp_path).model_copy(update={"rss_limit_gib": 0.001})
    output = tmp_path / "runs" / "rss-limit" / "output"

    reap_systematic_child(
        OverLimitProcess(),
        SystematicChildSupervisorConfig(output, config.max_runtime_seconds, config.rss_limit_gib),
        lambda _process_id: 2 * 1024 * 1024,
    )

    report = (output / "autonomous_research_cycle_ko.md").read_text(encoding="utf-8")
    assert "systematic_child_rss_limit_exceeded" in report
    assert terminated == [(4242, 15)]
