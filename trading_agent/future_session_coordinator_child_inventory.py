from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    canonical_service_config_sha256,
)
from trading_agent.future_session_us_activation_verifier import read_private_file
from trading_agent.private_immutable_file import publish_private_immutable_text

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


def cleanup_owned_child_jobs(
    config: FutureSessionCoordinatorServiceConfig,
    domain: str,
    runner: ChildStatusRunner,
) -> bool:
    try:
        labels = _owned_child_labels(config)
        for label in sorted(labels):
            target = f"{domain}/{label}"
            status = runner(("/bin/launchctl", "print", target), ())
            if status == 0:
                _ = runner(("/bin/launchctl", "bootout", target), ())
                status = runner(("/bin/launchctl", "print", target), ())
            if status != _NOT_LOADED_RETURN_CODE:
                raise ValueError
            _remove_owned_plist(config.launch_agents_dir / f"{label}.plist")
        _publish_cleanup_receipt(config, labels)
    except (OSError, TypeError, ValueError):
        sys.stderr.write("replace_candidate_child_cleanup_failed\n")
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
    labels.update(_artifact_child_labels(config))
    return labels


def _artifact_child_labels(config: FutureSessionCoordinatorServiceConfig) -> set[str]:
    labels: set[str] = set()
    artifact_root = config.state_root / "artifacts"
    if not artifact_root.exists():
        return labels
    for path in artifact_root.glob("*/*/preparation-manifest.json"):
        labels.update(_manifest_labels(path))
    return labels


def _remove_owned_plist(path: Path) -> None:
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise ValueError
            os.unlink(path.name, dir_fd=directory)
            if os.fstat(descriptor).st_nlink != 0:
                raise ValueError
            try:
                os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError
            os.fsync(directory)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _publish_cleanup_receipt(
    config: FutureSessionCoordinatorServiceConfig,
    labels: set[str],
) -> None:
    config_sha256 = canonical_service_config_sha256(config)
    payload = (
        json.dumps(
            {
                "config_sha256": config_sha256,
                "labels": sorted(labels),
                "result": "absent",
                "scheduler_main_sha": config.scheduler_main_sha,
                "schema_version": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    _ = publish_private_immutable_text(
        config.state_root / "replacement-child-cleanup" / f"{config_sha256}.json",
        payload,
    )


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


__all__ = ("cleanup_owned_child_jobs", "require_no_loaded_child_jobs")
