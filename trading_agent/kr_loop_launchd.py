from __future__ import annotations

import hashlib
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.kr_loop_active_release import resolve_active_source
from trading_agent.kr_loop_automation_config import (
    KrLoopAutomationConfig,
    kr_loop_automation_config_sha256,
    load_kr_loop_automation_config,
)
from trading_agent.kr_loop_launchd_install import install_kr_loop_launch_agents
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    load_research_agent_service_config,
)

KR_LOOP_AUTOMATION_LABEL: Final = "ai.trading-agent.kr-loop-automation"
KR_LOOP_INVOCATIONS: Final = tuple(
    (weekday, hour, minute) for weekday in range(2, 7) for hour, minute in ((16, 30), (18, 30))
)


class InvalidKrLoopLaunchAgentError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop LaunchAgent contract is invalid"


@dataclass(frozen=True, slots=True)
class KrLoopLaunchAgentPaths:
    research_agent: Path
    loop_engineer: Path


@dataclass(frozen=True, slots=True)
class KrLoopLaunchAgentVerification:
    ready: bool
    research_sha256: str
    loop_sha256: str


def kr_loop_launch_agent_paths(config: KrLoopAutomationConfig) -> KrLoopLaunchAgentPaths:
    version = kr_loop_automation_config_sha256(config)[:16]
    return KrLoopLaunchAgentPaths(
        research_agent=config.launch_agents_directory / f"{RESEARCH_AGENT_SERVICE_LABEL}-active-{version}.plist",
        loop_engineer=config.launch_agents_directory / f"{KR_LOOP_AUTOMATION_LABEL}-{version}.plist",
    )


def provision_kr_loop_launch_agents(
    config: KrLoopAutomationConfig,
    config_path: Path,
) -> KrLoopLaunchAgentPaths:
    try:
        paths = kr_loop_launch_agent_paths(config)
        _ = publish_private_immutable_text(paths.research_agent, _research_plist(config))
        _ = publish_private_immutable_text(paths.loop_engineer, _loop_plist(config, config_path))
        _ = verify_kr_loop_launch_agents(config_path)
        return paths
    except (InvalidKrLoopLaunchAgentError, InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidKrLoopLaunchAgentError from None


def verify_kr_loop_launch_agents(config_path: Path) -> KrLoopLaunchAgentVerification:
    try:
        config = load_kr_loop_automation_config(config_path)
        research = load_research_agent_service_config(config.research_agent_config)
        if research.project_root != config.repository:
            raise InvalidKrLoopLaunchAgentError
        _ = resolve_active_source(
            config.active_release,
            config.repository,
            KrLoopReleaseArtifactStore(config.artifact_root),
        )
        paths = kr_loop_launch_agent_paths(config)
        research_payload = read_private_text(paths.research_agent)
        loop_payload = read_private_text(paths.loop_engineer)
        if research_payload != _research_plist(config) or loop_payload != _loop_plist(config, config_path):
            raise InvalidKrLoopLaunchAgentError
        return KrLoopLaunchAgentVerification(
            ready=True,
            research_sha256=hashlib.sha256(research_payload.encode()).hexdigest(),
            loop_sha256=hashlib.sha256(loop_payload.encode()).hexdigest(),
        )
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidKrLoopLaunchAgentError from None


def _research_plist(config: KrLoopAutomationConfig) -> str:
    payload = {
        "KeepAlive": True,
        "Label": RESEARCH_AGENT_SERVICE_LABEL,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.repository / "run_active_research_agent_runtime.py"),
            "run",
            "--active-release",
            str(config.active_release),
            "--repository",
            str(config.repository),
            "--artifact-root",
            str(config.artifact_root),
            "--config",
            str(config.research_agent_config),
        ],
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
        "WorkingDirectory": str(config.repository),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode()


def _loop_plist(config: KrLoopAutomationConfig, config_path: Path) -> str:
    payload = {
        "Label": KR_LOOP_AUTOMATION_LABEL,
        "LowPriorityIO": True,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.repository / "run_kr_loop_automation.py"),
            "tick",
            "--config",
            str(config_path.expanduser().absolute()),
        ],
        "RunAtLoad": False,
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday, hour, minute in KR_LOOP_INVOCATIONS
        ],
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 60,
        "Umask": 0o077,
        "WorkingDirectory": str(config.repository),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode()


__all__ = (
    "KR_LOOP_AUTOMATION_LABEL",
    "KR_LOOP_INVOCATIONS",
    "InvalidKrLoopLaunchAgentError",
    "KrLoopLaunchAgentPaths",
    "KrLoopLaunchAgentVerification",
    "install_kr_loop_launch_agents",
    "kr_loop_launch_agent_paths",
    "provision_kr_loop_launch_agents",
    "verify_kr_loop_launch_agents",
)
