from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Protocol


class LaunchAgentConfig(Protocol):
    @property
    def label(self) -> str: ...

    @property
    def project_root(self) -> Path: ...

    @property
    def uv_path(self) -> Path: ...


def research_agent_launch_agent_text(config: LaunchAgentConfig, config_path: Path) -> str:
    payload = {
        "KeepAlive": True,
        "Label": config.label,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / "run_research_agent_runtime.py"),
            "run",
            "--config",
            str(config_path.expanduser().absolute()),
        ],
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
        "WorkingDirectory": str(config.project_root),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")
