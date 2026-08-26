from __future__ import annotations

import json
import os
import plistlib
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

import trading_agent.private_immutable_file as private_file
from trading_agent.local_browser_gateway_config import (
    LOCAL_BROWSER_GATEWAY_LABEL,
    InvalidLocalBrowserGatewayConfigError,
    LocalBrowserGatewayConfig,
    canonical_local_browser_gateway_config_sha256,
    load_local_browser_gateway_config,
    verify_local_browser_launch_agent,
    write_local_browser_gateway_config,
    write_local_browser_launch_agent,
)
from trading_agent.private_immutable_file import read_private_text


@dataclass(frozen=True, slots=True)
class GatewayFixture:
    config: LocalBrowserGatewayConfig
    config_path: Path
    plist_path: Path


@pytest.fixture
def gateway_fixture(tmp_path: Path) -> GatewayFixture:
    project_root = tmp_path / "project"
    project_root.mkdir()
    gateway_script = project_root / "run_local_browser_gateway.py"
    gateway_script.write_text("pass\n", encoding="utf-8")
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    uv_path = binaries / "uv"
    chrome_executable = binaries / "chrome"
    for executable in (uv_path, chrome_executable):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    config = LocalBrowserGatewayConfig(
        project_root=project_root,
        uv_path=uv_path,
        chrome_executable=chrome_executable,
        state_root=tmp_path / "runtime" / "state",
        profile_root=tmp_path / "runtime" / "profile",
        socket_path=tmp_path / "runtime" / "state" / "gateway.sock",
        receipt_database=tmp_path / "runtime" / "state" / "receipts.sqlite3",
        screenshot_root=tmp_path / "runtime" / "state" / "screenshots",
    )
    return GatewayFixture(config, private / "gateway.json", private / "gateway.plist")


def _config_from(config: LocalBrowserGatewayConfig, **changes: Path) -> LocalBrowserGatewayConfig:
    payload = config.model_dump(mode="python")
    payload.update(changes)
    return LocalBrowserGatewayConfig.model_validate(payload)


def _write_contract(fixture: GatewayFixture) -> None:
    assert write_local_browser_gateway_config(fixture.config_path, fixture.config)
    assert write_local_browser_launch_agent(fixture.plist_path, fixture.config, fixture.config_path)


def test_config_round_trips_with_canonical_hash_when_private_fixture_is_valid(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private gateway configuration.
    # When: it is published then loaded.
    assert write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config)
    loaded = load_local_browser_gateway_config(gateway_fixture.config_path)
    # Then: its immutable canonical representation and hash are preserved.
    assert loaded == gateway_fixture.config
    assert read_private_text(gateway_fixture.config_path).endswith("\n")
    assert canonical_local_browser_gateway_config_sha256(loaded) == canonical_local_browser_gateway_config_sha256(
        gateway_fixture.config
    )
    assert stat.S_IMODE(gateway_fixture.config_path.stat().st_mode) == 0o600


def test_launch_agent_has_exact_deterministic_contract_when_config_is_valid(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid configuration and private artifact paths.
    # When: the LaunchAgent is published twice.
    _write_contract(gateway_fixture)
    assert not write_local_browser_launch_agent(
        gateway_fixture.plist_path, gateway_fixture.config, gateway_fixture.config_path
    )
    payload = read_private_text(gateway_fixture.plist_path)
    # Then: its parsed arguments and service settings are deterministic and complete.
    document = plistlib.loads(payload.encode("utf-8"))
    assert document == {
        "KeepAlive": True,
        "Label": LOCAL_BROWSER_GATEWAY_LABEL,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(gateway_fixture.config.uv_path),
            "run",
            "--offline",
            "python",
            str(gateway_fixture.config.project_root / "run_local_browser_gateway.py"),
            "run",
            "--config",
            str(gateway_fixture.config_path),
        ],
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
    }
    assert all(
        forbidden not in payload.lower()
        for forbidden in (
            "secret",
            "token",
            "cookie",
            "credential",
            "account",
            "header",
            "environment",
            "codex",
            "chat",
            "thread",
        )
    )
    assert stat.S_IMODE(gateway_fixture.plist_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("artifact", ("config", "plist"))
def test_private_artifact_is_rejected_when_group_readable(gateway_fixture: GatewayFixture, artifact: str) -> None:
    # Given: a published private configuration or plist whose mode is weakened.
    _write_contract(gateway_fixture)
    path = gateway_fixture.config_path if artifact == "config" else gateway_fixture.plist_path
    path.chmod(0o640)
    # When: the affected artifact is loaded or verified.
    # Then: the private-file boundary fails closed with a stable typed error.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        if artifact == "config":
            _ = load_local_browser_gateway_config(path)
        else:
            _ = verify_local_browser_launch_agent(gateway_fixture.config_path, path)
    assert raised.value.reason in {
        "local_browser_gateway_config_read_invalid",
        "local_browser_launch_agent_verify_invalid",
    }


@pytest.mark.parametrize("artifact", ("config", "plist"))
def test_private_artifact_is_rejected_when_symlinked(gateway_fixture: GatewayFixture, artifact: str) -> None:
    # Given: a published artifact replaced with a symlink.
    _write_contract(gateway_fixture)
    path = gateway_fixture.config_path if artifact == "config" else gateway_fixture.plist_path
    target = path.with_name(f"{path.name}.target")
    target.write_text("fixture\n", encoding="utf-8")
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)
    # When: its trust boundary is crossed.
    # Then: the symlink is rejected before content is trusted.
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
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = load_local_browser_gateway_config(gateway_fixture.config_path)
    assert raised.value.reason == "local_browser_gateway_config_read_invalid"


def test_launch_agent_is_rejected_when_plist_text_is_noncanonical(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private LaunchAgent with harmless noncanonical whitespace.
    _write_contract(gateway_fixture)
    gateway_fixture.plist_path.write_text(read_private_text(gateway_fixture.plist_path) + "\n", encoding="utf-8")
    # When: the artifact is verified.
    # Then: exact canonical replay rejects it.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path)
    assert raised.value.reason == "local_browser_launch_agent_verify_invalid"


def test_launch_agent_is_rejected_when_contract_text_changes(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private config and a changed private plist contract.
    _write_contract(gateway_fixture)
    document = plistlib.loads(read_private_text(gateway_fixture.plist_path).encode("utf-8"))
    document["KeepAlive"] = False
    gateway_fixture.plist_path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True))
    # When: the contract is verified.
    # Then: replay mismatch is rejected.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path)
    assert raised.value.reason == "local_browser_launch_agent_verify_invalid"


@pytest.mark.parametrize(
    ("relationship", "reason"),
    (
        ("relative", "local_browser_gateway_path_not_absolute"),
        ("outside", "local_browser_gateway_state_descendant_invalid"),
        ("overlap", "local_browser_gateway_roots_overlap"),
        ("inside", "local_browser_gateway_root_inside_project"),
    ),
)
def test_config_rejects_invalid_path_relationship_when_constructed(
    gateway_fixture: GatewayFixture, relationship: str, reason: str
) -> None:
    # Given: a valid config with one invalid path relationship.
    # When: strict Pydantic parsing constructs it.
    # Then: construction fails with the invariant's stable reason.
    invalid_state = gateway_fixture.config.project_root / "runtime"
    changes = {
        "relative": {"project_root": Path("relative")},
        "outside": {"socket_path": gateway_fixture.config.project_root / "gateway.sock"},
        "overlap": {"profile_root": gateway_fixture.config.state_root / "profile"},
        "inside": {
            "state_root": invalid_state,
            "socket_path": invalid_state / "gateway.sock",
            "receipt_database": invalid_state / "receipts.sqlite3",
            "screenshot_root": invalid_state / "screenshots",
        },
    }[relationship]
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = _config_from(gateway_fixture.config, **changes)
    assert raised.value.reason == reason


@pytest.mark.parametrize(
    "required", ("uv_nonexecutable", "chrome_nonexecutable", "uv_missing", "chrome_missing", "gateway_script_missing")
)
def test_launch_agent_is_not_ready_when_required_file_is_missing_or_not_executable(
    gateway_fixture: GatewayFixture, required: str
) -> None:
    # Given: a valid published contract with one broken runtime binding.
    _write_contract(gateway_fixture)
    target = {
        "uv_nonexecutable": gateway_fixture.config.uv_path,
        "chrome_nonexecutable": gateway_fixture.config.chrome_executable,
        "uv_missing": gateway_fixture.config.uv_path,
        "chrome_missing": gateway_fixture.config.chrome_executable,
        "gateway_script_missing": gateway_fixture.config.project_root / "run_local_browser_gateway.py",
    }[required]
    if required.endswith("missing"):
        target.unlink()
    else:
        target.chmod(0o600)
    # When: readiness is verified.
    # Then: it fails closed rather than claiming service readiness.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path)
    assert raised.value.reason == "local_browser_launch_agent_verify_invalid"


@pytest.mark.parametrize("required", ("uv_path", "chrome_executable", "gateway_script"))
def test_launch_agent_is_not_ready_when_required_binding_is_symlinked(
    gateway_fixture: GatewayFixture, required: str
) -> None:
    # Given: a valid contract whose required executable or script is replaced by a symlink.
    _write_contract(gateway_fixture)
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
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path)
    assert raised.value.reason == "local_browser_launch_agent_verify_invalid"


def test_private_config_is_rejected_when_owner_is_not_current_user(
    gateway_fixture: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a published private config read under a mismatched current-user identity.
    assert write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config)
    current_uid = os.getuid()
    monkeypatch.setattr(private_file.os, "getuid", lambda: current_uid + 1)
    # When: the private boundary loads it.
    # Then: owner verification fails closed.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = load_local_browser_gateway_config(gateway_fixture.config_path)
    assert raised.value.reason == "local_browser_gateway_config_read_invalid"


def test_launch_agent_verifies_when_fixture_provides_required_bindings(gateway_fixture: GatewayFixture) -> None:
    # Given: a canonical private contract and fake executable bindings.
    _write_contract(gateway_fixture)
    # When: readiness is verified without creating runtime state.
    result = verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path)
    # Then: it reports deterministic hashes and ready status.
    assert result.ready is True
    assert result.config_sha256 == canonical_local_browser_gateway_config_sha256(gateway_fixture.config)
    assert result.plist_sha256
    assert not gateway_fixture.config.state_root.exists()


def test_artifact_boundaries_reject_relative_paths_when_called(
    gateway_fixture: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a relative private config path at a boundary.
    monkeypatch.chdir(gateway_fixture.config_path.parent)
    # When: publishing is requested through the config boundary.
    # Then: it rejects ambiguity without creating an artifact.
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = write_local_browser_gateway_config(Path("gateway.json"), gateway_fixture.config)
    assert raised.value.reason == "local_browser_gateway_config_write_invalid"
