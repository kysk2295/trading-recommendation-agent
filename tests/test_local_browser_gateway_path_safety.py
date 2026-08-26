from __future__ import annotations

from pathlib import Path

import pytest

from tests.local_browser_gateway_config_support import (
    CONFIG_READ_ERROR,
    LAUNCH_VERIFY_ERROR,
    SYMLINK_COMPONENT_ERROR,
    GatewayFixture,
    build_gateway_fixture,
    config_from,
    rejection,
    write_contract,
)
from trading_agent.local_browser_gateway_config import (
    load_local_browser_gateway_config,
    verify_local_browser_launch_agent,
    write_local_browser_gateway_config,
)


@pytest.fixture
def gateway_fixture(tmp_path: Path) -> GatewayFixture:
    return build_gateway_fixture(tmp_path)


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
    assert rejection(lambda: config_from(gateway_fixture.config, **changes)).reason == reason


@pytest.mark.parametrize(
    "field",
    ("project_root state_root profile_root socket_path receipt_database screenshot_root intermediate").split(),  # noqa: SIM905
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
    assert rejection(lambda: config_from(gateway_fixture.config, **changes)).reason == SYMLINK_COMPONENT_ERROR


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
        write_contract(gateway_fixture)
    project_target = gateway_fixture.config.project_root.with_name("project.target")
    gateway_fixture.config.project_root.rename(project_target)
    gateway_fixture.config.project_root.symlink_to(project_target, target_is_directory=True)
    operations = {
        "model": lambda: config_from(gateway_fixture.config),
        "write": lambda: write_local_browser_gateway_config(gateway_fixture.config_path, gateway_fixture.config),
        "load": lambda: load_local_browser_gateway_config(gateway_fixture.config_path),
        "verify": lambda: verify_local_browser_launch_agent(gateway_fixture.config_path, gateway_fixture.plist_path),
    }
    # When: each trust-boundary entrypoint validates the same path-bearing config.
    # Then: all paths fail closed with their stable boundary reason.
    assert rejection(operations[entrypoint]).reason == reason
