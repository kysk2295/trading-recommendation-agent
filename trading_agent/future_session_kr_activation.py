from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trading_agent.future_session_kr_activation_verifier import (
    VerifiedKrActivation,
    verify_kr_future_session_activation,
)
from trading_agent.future_session_us_activation import (
    copy_private_file,
    default_launchctl_runner,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
    LaunchctlRunner,
)
from trading_agent.future_session_us_activation_verifier import (
    PRIVATE_FILE_MODE,
    prepare_launch_agents_directory,
)

_NOT_LOADED_RETURN_CODE = 113


@dataclass(frozen=True, slots=True)
class KrFutureSessionActivation:
    label: str
    installed_plist: Path
    receipt_path: Path


def activate_kr_future_session(
    *,
    manifest_path: Path,
    launch_agents_dir: Path | None = None,
    launchctl_runner: LaunchctlRunner | None = None,
) -> KrFutureSessionActivation:
    launch_agents = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    verified = verify_kr_future_session_activation(
        manifest_path=manifest_path,
        launch_agents_dir=launch_agents,
    )
    claimed = (
        verified.receipt_path,
        Path(f"{verified.receipt_path}.claim"),
        verified.installed_plist,
    )
    if any(os.path.lexists(path) for path in claimed):
        raise FutureSessionActivationError("activation_already_claimed")
    runner = launchctl_runner or default_launchctl_runner
    domain = f"gui/{os.getuid()}"
    probe = runner(("print", f"{domain}/{verified.label}"))
    if probe == 0:
        raise FutureSessionActivationError("launchctl_label_already_loaded")
    if probe != _NOT_LOADED_RETURN_CODE:
        raise FutureSessionActivationError("launchctl_probe_failed")
    prepare_launch_agents_directory(launch_agents)
    _install_and_bootstrap(verified, domain=domain, runner=runner)
    return KrFutureSessionActivation(
        label=verified.label,
        installed_plist=verified.installed_plist,
        receipt_path=verified.receipt_path,
    )


def _install_and_bootstrap(
    verified: VerifiedKrActivation,
    *,
    domain: str,
    runner: LaunchctlRunner,
) -> None:
    installed = False
    bootstrapped = False
    try:
        copy_private_file(verified.source_plist, verified.installed_plist)
        installed = True
        if runner(("bootstrap", domain, str(verified.installed_plist))) != 0:
            raise FutureSessionActivationError("launchctl_bootstrap_failed")
        bootstrapped = True
        _write_receipt(verified)
    except (FutureSessionActivationError, OSError):
        if bootstrapped:
            _ = runner(("bootout", domain, str(verified.installed_plist)))
        if installed:
            verified.installed_plist.unlink(missing_ok=True)
        raise


def _write_receipt(verified: VerifiedKrActivation) -> None:
    payload = (
        json.dumps(
            {
                "label": verified.label,
                "manifest_sha256": verified.manifest_sha256,
                "result": "activated",
                "schema_version": 1,
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
        if os.write(descriptor, payload) != len(payload):
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


__all__ = (
    "KrFutureSessionActivation",
    "activate_kr_future_session",
)
