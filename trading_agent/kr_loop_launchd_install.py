from __future__ import annotations

import datetime as dt
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import override

from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
    load_research_agent_service_config,
)
from trading_agent.research_agent_service_health import (
    HealthEvaluator,
    ResearchAgentServiceHealthEvaluation,
    await_fresh_research_agent_service_health,
    evaluate_persisted_research_agent_service_health,
)

LaunchctlRunner = Callable[[tuple[str, ...]], int]
Clock = Callable[[], dt.datetime]


class InvalidKrLoopLaunchAgentInstallError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop LaunchAgent installation failed"


def install_kr_loop_launch_agents(
    config_path: Path,
    current_research_plist: Path,
    *,
    runner: LaunchctlRunner | None = None,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    health_evaluator: HealthEvaluator | None = None,
) -> bool:
    from trading_agent.kr_loop_automation_config import load_kr_loop_automation_config
    from trading_agent.kr_loop_launchd import kr_loop_launch_agent_paths, verify_kr_loop_launch_agents

    automation = load_kr_loop_automation_config(config_path)
    _ = verify_kr_loop_launch_agents(config_path)
    research = load_research_agent_service_config(automation.research_agent_config)
    paths = kr_loop_launch_agent_paths(automation)
    old_path = current_research_plist.expanduser().absolute()
    if old_path.is_symlink() or not old_path.is_file():
        raise InvalidKrLoopLaunchAgentInstallError
    active = _launchctl if runner is None else runner
    evaluator = _health_evaluator if health_evaluator is None else health_evaluator
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"
    old = str(old_path)
    new = str(paths.research_agent)
    if active(("/bin/launchctl", "bootout", domain, old)) != 0:
        raise InvalidKrLoopLaunchAgentInstallError
    started_at = clock()
    if (
        active(("/bin/launchctl", "bootstrap", domain, new)) != 0
        or active(("/bin/launchctl", "kickstart", target)) != 0
    ):
        return _restore(active, domain, target, old, new)
    health = await_fresh_research_agent_service_health(research, started_at, clock, evaluator)
    if not health.accepted:
        return _restore(active, domain, target, old, new)
    if active(("/bin/launchctl", "bootstrap", domain, str(paths.loop_engineer))) == 0:
        return True
    _ = active(("/bin/launchctl", "bootout", domain, new))
    return _restore(active, domain, target, old, new)


def _health_evaluator(
    config: ResearchAgentServiceConfig,
    started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> ResearchAgentServiceHealthEvaluation:
    return evaluate_persisted_research_agent_service_health(
        config.output_root,
        canonical_research_agent_service_config_sha256(config),
        started_at,
        evaluated_at,
    )


def _restore(runner: LaunchctlRunner, domain: str, target: str, old: str, new: str) -> bool:
    _ = runner(("/bin/launchctl", "bootout", domain, new))
    if runner(("/bin/launchctl", "bootstrap", domain, old)) != 0:
        raise InvalidKrLoopLaunchAgentInstallError
    _ = runner(("/bin/launchctl", "kickstart", target))
    return False


def _launchctl(command: tuple[str, ...]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


__all__ = (
    "Clock",
    "InvalidKrLoopLaunchAgentInstallError",
    "LaunchctlRunner",
    "install_kr_loop_launch_agents",
)
