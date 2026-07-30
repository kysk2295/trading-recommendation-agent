from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from trading_agent.future_session_us_activation_models import (
    ActivatedUsRoleArtifact,
    FutureSessionActivation,
    FutureSessionActivationError,
    LaunchctlRunner,
)
from trading_agent.future_session_us_activation_verifier import (
    PRIVATE_FILE_MODE,
    VerifiedActivation,
    prepare_launch_agents_directory,
    read_private_file,
    verify_us_future_session_activation,
)

_NOT_LOADED_RETURN_CODE = 113


def activate_us_future_session(
    *,
    manifest_path: Path,
    launch_agents_dir: Path | None = None,
    launchctl_runner: LaunchctlRunner | None = None,
) -> FutureSessionActivation:
    resolved_launch_agents_dir = (
        Path.home() / "Library" / "LaunchAgents" if launch_agents_dir is None else launch_agents_dir
    )
    verified = verify_us_future_session_activation(
        manifest_path=manifest_path,
        launch_agents_dir=resolved_launch_agents_dir,
    )
    verify_unclaimed(verified.entries, verified.receipt_path)
    runner = default_launchctl_runner if launchctl_runner is None else launchctl_runner
    user_domain = f"gui/{os.getuid()}"
    for entry in verified.entries:
        probe = runner(("print", f"{user_domain}/{entry.label}"))
        if probe == 0:
            raise FutureSessionActivationError("launchctl_label_already_loaded")
        if probe != _NOT_LOADED_RETURN_CODE:
            raise FutureSessionActivationError("launchctl_probe_failed")
    prepare_launch_agents_directory(resolved_launch_agents_dir)
    install_and_bootstrap(
        verified,
        user_domain=user_domain,
        launchctl_runner=runner,
    )
    return FutureSessionActivation(
        entries=verified.entries,
        receipt_path=verified.receipt_path,
    )


def verify_unclaimed(entries: tuple[ActivatedUsRoleArtifact, ...], receipt_path: Path) -> None:
    paths = (receipt_path, Path(f"{receipt_path}.claim"), *(entry.installed_plist for entry in entries))
    if any(os.path.lexists(path) for path in paths):
        raise FutureSessionActivationError("activation_already_claimed")
    for entry in entries:
        receipt = entry.source_plist.parent.parent / "receipts" / f"{entry.role.value}.json"
        if os.path.lexists(receipt) or os.path.lexists(f"{receipt}.claim"):
            raise FutureSessionActivationError("schedule_already_claimed")


def install_and_bootstrap(
    verified: VerifiedActivation,
    *,
    user_domain: str,
    launchctl_runner: LaunchctlRunner,
) -> None:
    installed: list[ActivatedUsRoleArtifact] = []
    bootstrapped: list[ActivatedUsRoleArtifact] = []
    try:
        for entry in verified.entries:
            copy_private_file(entry.source_plist, entry.installed_plist)
            installed.append(entry)
        for entry in verified.entries:
            if launchctl_runner(("bootstrap", user_domain, str(entry.installed_plist))) != 0:
                raise FutureSessionActivationError("launchctl_bootstrap_failed")
            bootstrapped.append(entry)
        write_activation_receipt(verified)
    except (FutureSessionActivationError, OSError):
        for entry in reversed(bootstrapped):
            _ = launchctl_runner(("bootout", user_domain, str(entry.installed_plist)))
        for entry in reversed(installed):
            entry.installed_plist.unlink(missing_ok=True)
        raise


def copy_private_file(source: Path, destination: Path) -> None:
    content = read_private_file(source, PRIVATE_FILE_MODE)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        written = os.write(descriptor, content)
        if written != len(content):
            raise OSError("short write")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
    except OSError:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def write_activation_receipt(verified: VerifiedActivation) -> None:
    payload = (
        json.dumps(
            {
                "labels": [entry.label for entry in verified.entries],
                "manifest_sha256": verified.manifest_sha256,
                "result": "activated",
                "schema_version": 2,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{verified.receipt_path.name}.",
        dir=verified.receipt_path.parent,
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("short write")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        os.link(temporary, verified.receipt_path, follow_symlinks=False)
        temporary.unlink()
    except OSError:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def default_launchctl_runner(arguments: tuple[str, ...]) -> int:
    completed = subprocess.run(("/bin/launchctl", *arguments), check=False, capture_output=True, text=True)
    return completed.returncode


__all__ = (
    "ActivatedUsRoleArtifact",
    "FutureSessionActivation",
    "FutureSessionActivationError",
    "LaunchctlRunner",
    "activate_us_future_session",
)
