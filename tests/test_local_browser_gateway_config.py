from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path

import pytest

import trading_agent.private_immutable_file as private_file
from tests.local_browser_gateway_config_support import (
    CONFIG_READ_ERROR,
    LAUNCH_VERIFY_ERROR,
    GatewayFixture,
    build_gateway_fixture,
    load_rejection,
    replace_with_symlink,
    verify_rejection,
    write_contract,
)
from trading_agent.local_browser_gateway_config import (
    LOCAL_BROWSER_GATEWAY_LABEL,
    InvalidLocalBrowserGatewayConfigError,
    canonical_local_browser_gateway_config_sha256,
    load_local_browser_gateway_config,
    verify_local_browser_launch_agent,
    write_local_browser_gateway_config,
)
from trading_agent.private_immutable_file import read_private_text


@pytest.fixture
def gateway_fixture(tmp_path: Path) -> GatewayFixture:
    return build_gateway_fixture(tmp_path)


def test_launch_agent_has_exact_deterministic_contract_when_config_is_valid(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid configuration and private artifact paths.
    # When: the LaunchAgent is published twice.
    write_contract(gateway_fixture)
    loaded = load_local_browser_gateway_config(gateway_fixture.config_path)
    payload = read_private_text(gateway_fixture.plist_path)
    # Then: its parsed arguments and service settings are deterministic and complete.
    document = plistlib.loads(payload.encode("utf-8"))
    gateway_script = gateway_fixture.config.project_root / "run_local_browser_gateway.py"
    arguments = [str(gateway_fixture.config.uv_path), "run", "--offline", "python", str(gateway_script)]
    arguments += ["run", "--config", str(gateway_fixture.config_path)]
    assert document == {
        "KeepAlive": True,
        "Label": LOCAL_BROWSER_GATEWAY_LABEL,
        "ProcessType": "Background",
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
    }
    assert "EnvironmentVariables" not in document
    assert loaded == gateway_fixture.config
    assert verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path).ready is True
    assert canonical_local_browser_gateway_config_sha256(loaded) == canonical_local_browser_gateway_config_sha256(
        gateway_fixture.config
    )


@pytest.mark.parametrize(
    ("artifact", "weakening"),
    (("config", "mode"), ("plist", "mode"), ("config", "symlink"), ("plist", "symlink")),
)
def test_private_artifact_is_rejected_when_not_private(
    gateway_fixture: GatewayFixture, artifact: str, weakening: str
) -> None:
    # Given: a published artifact with weakened mode or a symlink final component.
    write_contract(gateway_fixture)
    path = gateway_fixture.config_path if artifact == "config" else gateway_fixture.plist_path
    actions = {"mode": lambda: path.chmod(0o640), "symlink": lambda: replace_with_symlink(path)}
    actions[weakening]()
    # When: its private boundary is crossed.
    # Then: the unsafe artifact is rejected with a typed boundary error.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError):
        if artifact == "config":
            _ = load_local_browser_gateway_config(path)
        else:
            _ = verify_local_browser_launch_agent(gateway_fixture.config_path, path)


def test_config_is_rejected_when_json_is_noncanonical(gateway_fixture: GatewayFixture) -> None:
    # Given: a private configuration altered to equivalent but noncanonical JSON.
    assert write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config)
    payload = json.dumps(gateway_fixture.config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    gateway_fixture.config_path.write_text(payload, encoding="utf-8")
    # When: it is loaded.
    # Then: the canonical-replay boundary rejects it.
    assert load_rejection(gateway_fixture).reason == CONFIG_READ_ERROR


def test_launch_agent_is_rejected_when_plist_text_is_noncanonical(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private LaunchAgent with harmless noncanonical whitespace.
    write_contract(gateway_fixture)
    gateway_fixture.plist_path.write_text(read_private_text(gateway_fixture.plist_path) + "\n", encoding="utf-8")
    # When: the artifact is verified.
    # Then: exact canonical replay rejects it.
    assert verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


def test_launch_agent_is_rejected_when_contract_text_changes(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private config and a changed private plist contract.
    write_contract(gateway_fixture)
    document = plistlib.loads(read_private_text(gateway_fixture.plist_path).encode("utf-8"))
    document["KeepAlive"] = False
    gateway_fixture.plist_path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True))
    # When: the contract is verified.
    # Then: replay mismatch is rejected.
    assert verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


@pytest.mark.parametrize(
    "required", ("uv_nonexecutable", "chrome_nonexecutable", "uv_missing", "chrome_missing", "gateway_script_missing")
)
def test_launch_agent_is_not_ready_when_required_file_is_missing_or_not_executable(
    gateway_fixture: GatewayFixture, required: str
) -> None:
    # Given: a valid published contract with one broken runtime binding.
    write_contract(gateway_fixture)
    base = required.removesuffix("_nonexecutable").removesuffix("_missing")
    targets = {
        "uv": gateway_fixture.config.uv_path,
        "chrome": gateway_fixture.config.chrome_executable,
        "gateway_script": gateway_fixture.config.project_root / "run_local_browser_gateway.py",
    }
    target = targets[base]
    if required.endswith("missing"):
        target.unlink()
    else:
        target.chmod(0o600)
    # When: readiness is verified.
    # Then: it fails closed rather than claiming service readiness.
    assert verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


@pytest.mark.parametrize("required", ("uv_path", "chrome_executable", "gateway_script"))
def test_launch_agent_is_not_ready_when_required_binding_is_symlinked(
    gateway_fixture: GatewayFixture, required: str
) -> None:
    # Given: a valid contract whose required executable or script is replaced by a symlink.
    write_contract(gateway_fixture)
    target = {
        "uv_path": gateway_fixture.config.uv_path,
        "chrome_executable": gateway_fixture.config.chrome_executable,
        "gateway_script": gateway_fixture.config.project_root / "run_local_browser_gateway.py",
    }[required]
    linked_target = target.with_name(f"{target.name}.target")
    target.rename(linked_target)
    target.symlink_to(linked_target)
    # When: readiness is verified.
    # Then: the symlinked binding is rejected rather than followed.
    assert verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


def test_private_config_is_rejected_when_owner_is_not_current_user(
    gateway_fixture: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a published private config read under a mismatched current-user identity.
    assert write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config)
    current_uid = os.getuid()
    monkeypatch.setattr(private_file.os, "getuid", lambda: current_uid + 1)
    # When: the private boundary loads it.
    # Then: owner verification fails closed.
    assert load_rejection(gateway_fixture).reason == CONFIG_READ_ERROR
