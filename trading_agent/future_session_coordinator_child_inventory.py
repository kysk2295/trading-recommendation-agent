from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_us_activation_verifier import read_private_file

type ChildStatusRunner = Callable[[tuple[str, ...], tuple[int, ...]], int]

_PREFIXES = (
    "ai.trading-agent.us-",
    "ai.trading-agent.future-session.kr.",
)
_NOT_LOADED_RETURN_CODE = 113


def require_no_loaded_child_jobs(
    config: FutureSessionCoordinatorServiceConfig,
    domain: str,
    runner: ChildStatusRunner,
) -> bool:
    try:
        labels = _owned_child_labels(config)
    except (OSError, TypeError, ValueError):
        sys.stderr.write("replace_child_job_inventory_invalid\n")
        return False
    for label in sorted(labels):
        status = runner(("/bin/launchctl", "print", f"{domain}/{label}"), ())
        if status == 0:
            sys.stderr.write("replace_active_child_job\n")
            return False
        if status != _NOT_LOADED_RETURN_CODE:
            sys.stderr.write("replace_child_job_status_failed\n")
            return False
    return True


def _owned_child_labels(config: FutureSessionCoordinatorServiceConfig) -> set[str]:
    labels: set[str] = set()
    if config.launch_agents_dir.exists():
        labels.update(
            path.stem
            for path in config.launch_agents_dir.iterdir()
            if path.suffix == ".plist" and path.stem.startswith(_PREFIXES)
        )
    artifact_root = config.state_root / "artifacts"
    if not artifact_root.exists():
        return labels
    for path in artifact_root.glob("*/*/preparation-manifest.json"):
        labels.update(_manifest_labels(path))
    return labels


def _manifest_labels(path: Path) -> set[str]:
    market = path.parent.parent.name
    payload = json.loads(read_private_file(path, 0o600))
    if market == "us":
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError
    elif market == "kr":
        entries = [payload.get("entry")]
    else:
        raise ValueError
    expected_prefix = _PREFIXES[0] if market == "us" else _PREFIXES[1]
    labels: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError
        label = entry.get("label")
        if not isinstance(label, str) or not label.startswith(expected_prefix):
            raise ValueError
        labels.add(label)
    return labels


__all__ = ("require_no_loaded_child_jobs",)
