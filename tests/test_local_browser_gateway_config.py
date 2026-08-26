from __future__ import annotations

import json
import os
import plistlib
from collections.abc import Callable
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

CONFIG_READ_ERROR = "local_browser_gateway_config_read_invalid"
LAUNCH_VERIFY_ERROR = "local_browser_launch_agent_verify_invalid"
SYMLINK_COMPONENT_ERROR = "local_browser_gateway_symlink_component_invalid"


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


def _rejection[T](action: Callable[[], T]) -> InvalidLocalBrowserGatewayConfigError:
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = action()
    return raised.value


def _load_rejection(fixture: GatewayFixture) -> InvalidLocalBrowserGatewayConfigError:
    return _rejection(lambda: load_local_browser_gateway_config(fixture.config_path))


def _verify_rejection(fixture: GatewayFixture) -> InvalidLocalBrowserGatewayConfigError:
    return _rejection(lambda: verify_local_browser_launch_agent(fixture.config_path, fixture.plist_path))


def test_launch_agent_has_exact_deterministic_contract_when_config_is_valid(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid configuration and private artifact paths.
    # When: the LaunchAgent is published twice.
    _write_contract(gateway_fixture)
    loaded = load_local_browser_gateway_config(gateway_fixture.config_path)
    payload = read_private_text(gateway_fixture.plist_path)
    # Then: its parsed arguments and service settings are deterministic and complete.
    document = plistlib.loads(payload.encode("utf-8"))
    gateway_script = gateway_fixture.config.project_root / "run_local_browser_gateway.py"
    arguments = [str(gateway_fixture.config.uv_path), "run", "--offline", "python", str(gateway_script)]
    arguments += ["run", "--config", str(gateway_fixture.config_path)]
    assert document == {
        "KeepAlive": True, "Label": LOCAL_BROWSER_GATEWAY_LABEL, "ProcessType": "Background",
        "ProgramArguments": arguments, "RunAtLoad": True, "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null", "ThrottleInterval": 30, "Umask": 0o077,
    }
    assert "EnvironmentVariables" not in document
    assert loaded == gateway_fixture.config
    assert verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path).ready is True
    assert canonical_local_browser_gateway_config_sha256(loaded) == canonical_local_browser_gateway_config_sha256(
        gateway_fixture.config
    )


def _replace_with_symlink(path: Path) -> None:
    target = path.with_name(f"{path.name}.target")
    target.write_text("fixture\n", encoding="utf-8")
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)


@pytest.mark.parametrize(
    ("artifact", "weakening"),
    (("config", "mode"), ("plist", "mode"), ("config", "symlink"), ("plist", "symlink")),
)
def test_private_artifact_is_rejected_when_not_private(
    gateway_fixture: GatewayFixture, artifact: str, weakening: str
) -> None:
    # Given: a published artifact with weakened mode or a symlink final component.
    _write_contract(gateway_fixture)
    path = gateway_fixture.config_path if artifact == "config" else gateway_fixture.plist_path
    actions = {"mode": lambda: path.chmod(0o640), "symlink": lambda: _replace_with_symlink(path)}
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
    assert _load_rejection(gateway_fixture).reason == CONFIG_READ_ERROR


def test_launch_agent_is_rejected_when_plist_text_is_noncanonical(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private LaunchAgent with harmless noncanonical whitespace.
    _write_contract(gateway_fixture)
    gateway_fixture.plist_path.write_text(read_private_text(gateway_fixture.plist_path) + "\n", encoding="utf-8")
    # When: the artifact is verified.
    # Then: exact canonical replay rejects it.
    assert _verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


def test_launch_agent_is_rejected_when_contract_text_changes(gateway_fixture: GatewayFixture) -> None:
    # Given: a valid private config and a changed private plist contract.
    _write_contract(gateway_fixture)
    document = plistlib.loads(read_private_text(gateway_fixture.plist_path).encode("utf-8"))
    document["KeepAlive"] = False
    gateway_fixture.plist_path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True))
    # When: the contract is verified.
    # Then: replay mismatch is rejected.
    assert _verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


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
    assert _rejection(lambda: _config_from(gateway_fixture.config, **changes)).reason == reason


@pytest.mark.parametrize(
    "field",
    ("project_root state_root profile_root socket_path " "receipt_database screenshot_root intermediate").split(),  # noqa: SIM905
)
def test_model_rejects_existing_symlinked_config_component(gateway_fixture: GatewayFixture, field: str) -> None:
    # Given: real roots plus direct and intermediate symlink aliases.
    root = gateway_fixture.config.project_root.parent
    state = gateway_fixture.config.state_root
    state.mkdir(parents=True)
    state_target = state / "target"
    state_target.mkdir()
    alias = state / "alias"
    alias.symlink_to(state_target, target_is_directory=True)
    aliases = {
        "project_root": root / "project.alias",
        "state_root": root / "state.alias",
        "profile_root": root / "profile.alias",
    }
    bindings = (
        (aliases["project_root"], gateway_fixture.config.project_root),
        (aliases["state_root"], root / "state.target"),
        (aliases["profile_root"], root / "profile.target"),
    )
    for path, target in bindings:
        target.mkdir(exist_ok=True)
        path.symlink_to(target, target_is_directory=True)
    changes = {
        "project_root": {"project_root": aliases["project_root"]},
        "state_root": {"state_root": aliases["state_root"]},
        "profile_root": {"profile_root": aliases["profile_root"]},
        "socket_path": {"socket_path": alias},
        "receipt_database": {"receipt_database": alias},
        "screenshot_root": {"screenshot_root": alias},
        "intermediate": {"socket_path": alias / "gateway.sock"},
    }[field]
    # When: strict model validation crosses the config trust boundary.
    # Then: every existing symlink component is rejected before containment logic trusts it.
    assert _rejection(lambda: _config_from(gateway_fixture.config, **changes)).reason == SYMLINK_COMPONENT_ERROR


@pytest.mark.parametrize(
    ("entrypoint", "reason"),
    (
        ("model", SYMLINK_COMPONENT_ERROR),
        ("write", "local_browser_gateway_config_write_invalid"),
        ("load", CONFIG_READ_ERROR),
        ("verify", LAUNCH_VERIFY_ERROR),
    ),
)
def test_config_boundary_rejects_persisted_project_root_symlink(
    gateway_fixture: GatewayFixture, entrypoint: str, reason: str
) -> None:
    # Given: a valid config whose previously real project root becomes a symlink alias.
    if entrypoint in {"load", "verify"}:
        _write_contract(gateway_fixture)
    project_target = gateway_fixture.config.project_root.with_name("project.target")
    gateway_fixture.config.project_root.rename(project_target)
    gateway_fixture.config.project_root.symlink_to(project_target, target_is_directory=True)
    operations = {
        "model": lambda: _config_from(gateway_fixture.config),
        "write": lambda: write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config),
        "load": lambda: load_local_browser_gateway_config(gateway_fixture.config_path),
        "verify": lambda: verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path),
    }
    # When: each trust-boundary entrypoint validates the same path-bearing config.
    # Then: all paths fail closed with their stable boundary reason.
    assert _rejection(operations[entrypoint]).reason == reason


@pytest.mark.parametrize(
    "required", ("uv_nonexecutable", "chrome_nonexecutable", "uv_missing", "chrome_missing", "gateway_script_missing")
)
def test_launch_agent_is_not_ready_when_required_file_is_missing_or_not_executable(
    gateway_fixture: GatewayFixture, required: str
) -> None:
    # Given: a valid published contract with one broken runtime binding.
    _write_contract(gateway_fixture)
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
    assert _verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


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
    assert _verify_rejection(gateway_fixture).reason == LAUNCH_VERIFY_ERROR


def test_private_config_is_rejected_when_owner_is_not_current_user(
    gateway_fixture: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a published private config read under a mismatched current-user identity.
    assert write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config)
    current_uid = os.getuid()
    monkeypatch.setattr(private_file.os, "getuid", lambda: current_uid + 1)
    # When: the private boundary loads it.
    # Then: owner verification fails closed.
    assert _load_rejection(gateway_fixture).reason == CONFIG_READ_ERROR
