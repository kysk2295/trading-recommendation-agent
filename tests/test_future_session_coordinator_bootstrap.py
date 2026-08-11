from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

import run_future_session_coordinator_service as cli
from tests.test_future_session_coordinator_service import _repository
from tests.test_future_session_coordinator_service_ready import _ready_config
from trading_agent.future_session_coordinator_bootstrap import (
    FutureSessionCoordinatorBootstrapManifest,
    bootstrap_coordinator_bundle,
    canonical_bootstrap_manifest_json,
)
from trading_agent.future_session_coordinator_inspectors import inspect_request
from trading_agent.future_session_coordinator_service_runtime import load_service_config
from trading_agent.future_session_plan_models import FrozenRuntimeAuthority


def test_bootstrap_atomically_publishes_private_idempotent_bundle(tmp_path: Path) -> None:
    base = _ready_config(tmp_path)
    manifest = FutureSessionCoordinatorBootstrapManifest(
        bundle_path=(tmp_path / "bundles" / base.scheduler_main_sha).absolute(),
        state_root=(tmp_path / "service-state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=base.authority_repository,
        scheduler_main_sha=base.scheduler_main_sha,
        poll_interval_seconds=base.poll_interval_seconds,
        us_template=inspect_request(base.us_template_request_path),
        kr_template=inspect_request(base.kr_template_request_path),
    )

    first = bootstrap_coordinator_bundle(manifest)
    replay = bootstrap_coordinator_bundle(manifest)
    config = load_service_config(first)

    assert replay == first == manifest.bundle_path / "coordinator.json"
    assert config.us_template_request_path == manifest.bundle_path / "us-template.json"
    assert config.kr_template_request_path == manifest.bundle_path / "kr-template.json"
    assert stat.S_IMODE(manifest.bundle_path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in manifest.bundle_path.iterdir())


def test_bootstrap_cli_builds_reviewed_manifest_and_provisions_plist(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir(mode=0o700)
    base = _ready_config(fixture)
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    repository, commit = _repository(authority)
    frozen = FrozenRuntimeAuthority(directory=repository, commit_sha=commit)
    us = inspect_request(base.us_template_request_path).model_copy(
        update={
            "scheduler_main_sha": commit,
            "authority_repository": repository,
            "frozen_runtime": frozen,
        }
    )
    kr = inspect_request(base.kr_template_request_path).model_copy(
        update={
            "scheduler_main_sha": commit,
            "authority_repository": repository,
            "frozen_runtime": frozen,
        }
    )
    manifest = FutureSessionCoordinatorBootstrapManifest(
        bundle_path=(tmp_path / "bundles" / commit).absolute(),
        state_root=(tmp_path / "service-state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
        us_template=us,
        kr_template=kr,
    )
    manifest_path = (tmp_path / "bootstrap.json").absolute()
    manifest_path.write_text(canonical_bootstrap_manifest_json(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    code = cli.main(("bootstrap", "--manifest", str(manifest_path)))
    replay = cli.main(("bootstrap", "--manifest", str(manifest_path)))
    output = capsys.readouterr().out

    assert code == replay == 0
    assert '"result":"bootstrapped"' in output
    config = load_service_config(manifest.bundle_path / "coordinator.json")
    assert (config.launch_agents_dir / "ai.trading-agent.future-session-coordinator.plist").is_file()


@pytest.mark.parametrize(
    "overlap",
    ("state", "launch", "authority", "state_child"),
)
def test_bootstrap_manifest_rejects_overlapping_operational_roots(
    tmp_path: Path,
    overlap: str,
) -> None:
    base = _ready_config(tmp_path)
    bundle = (tmp_path / "bundle").absolute()
    state_root = bundle if overlap == "state" else (tmp_path / "state").absolute()
    launch_agents_dir = bundle if overlap == "launch" else (tmp_path / "launch").absolute()
    authority_repository = bundle if overlap == "authority" else base.authority_repository
    if overlap == "state_child":
        state_root = bundle / "state"

    with pytest.raises(ValidationError):
        FutureSessionCoordinatorBootstrapManifest(
            bundle_path=bundle,
            state_root=state_root,
            launch_agents_dir=launch_agents_dir,
            authority_repository=authority_repository,
            scheduler_main_sha=base.scheduler_main_sha,
            poll_interval_seconds=30,
            us_template=inspect_request(base.us_template_request_path),
            kr_template=inspect_request(base.kr_template_request_path),
        )


def test_bootstrap_manifest_rejects_unbounded_poll_interval(tmp_path: Path) -> None:
    base = _ready_config(tmp_path)

    with pytest.raises(ValidationError):
        FutureSessionCoordinatorBootstrapManifest(
            bundle_path=(tmp_path / "bundle").absolute(),
            state_root=(tmp_path / "state").absolute(),
            launch_agents_dir=(tmp_path / "launch").absolute(),
            authority_repository=base.authority_repository,
            scheduler_main_sha=base.scheduler_main_sha,
            poll_interval_seconds=10**100,
            us_template=inspect_request(base.us_template_request_path),
            kr_template=inspect_request(base.kr_template_request_path),
        )
