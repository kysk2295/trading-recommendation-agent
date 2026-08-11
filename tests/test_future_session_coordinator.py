from __future__ import annotations

import fcntl
import json
import os
import plistlib
from pathlib import Path

import pytest

import trading_agent.future_session_coordinator as coordinator_module
from tests.future_session_kr_support import kr_authority_files
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_coordinator import coordinate_future_session
from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorRequest,
)
from trading_agent.future_session_plan_models import ReadyToPrepareSessionPlan


class _LaunchdFixture:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: tuple[str, ...]) -> int:
        self.calls.append(arguments)
        match arguments[0]:
            case "print":
                return 0 if arguments[1].split("/", 2)[-1] in self.loaded else 113
            case "bootstrap":
                with Path(arguments[2]).open("rb") as handle:
                    label = plistlib.load(handle)["Label"]
                assert isinstance(label, str)
                self.loaded.add(label)
                return 0
            case command:
                pytest.fail(f"unexpected launchctl command: {command}")

    def is_loaded(self, label: str) -> bool:
        return label in self.loaded


@pytest.mark.parametrize("market", ["us", "kr"])
def test_first_run_activates_and_exact_replay_is_query_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market: str,
) -> None:
    # Given: one canonical ready request with no plan, preparation, or installation.
    fixture = _authority_files if market == "us" else kr_authority_files
    _request, plan, request_path, seeded_plan_path = fixture(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    expected_plan = seeded_plan_path.read_text(encoding="utf-8")
    plan_path = tmp_path / "coordinator-plan.json"
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launchd = _LaunchdFixture()
    request = FutureSessionCoordinatorRequest(
        request_path=request_path,
        plan_path=plan_path,
        launch_agents_dir=launch_agents,
    )
    materialize_calls = 0
    original = (
        coordinator_module.materialize_us_future_session
        if market == "us"
        else coordinator_module.materialize_kr_future_session
    )

    def counted(materialization):
        nonlocal materialize_calls
        materialize_calls += 1
        return original(materialization)

    monkeypatch.setattr(
        coordinator_module,
        f"materialize_{market}_future_session",
        counted,
    )
    seeded_plan_path.unlink()

    # When: the coordinator runs once and then receives the exact same authority.
    first = coordinate_future_session(
        request,
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )
    launchd.calls.clear()
    replay = coordinate_future_session(
        request,
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )

    # Then: first run creates one schedule; replay verifies it without any mutation call.
    assert first.result == "activated"
    assert first.preparation == "prepared"
    assert first.activation == "activated"
    assert replay.result == "activated"
    assert replay.preparation == "already_prepared"
    assert replay.activation == "already_activated"
    assert materialize_calls == 1
    assert launchd.calls == []
    assert plan_path.read_text(encoding="utf-8") == expected_plan


def test_waiting_authority_performs_zero_mutation(tmp_path: Path) -> None:
    # Given: a canonical request whose scheduler authority is unavailable.
    _request, _plan, request_path, _plan_path = _authority_files(tmp_path)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["scheduler_main_sha"] = "f" * 40
    request_path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    request_path.chmod(0o600)
    plan_path = tmp_path / "coordinator-plan.json"
    calls: list[tuple[str, ...]] = []

    # When
    receipt = coordinate_future_session(
        FutureSessionCoordinatorRequest(
            request_path=request_path,
            plan_path=plan_path,
            launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
        ),
        launchctl_runner=lambda arguments: calls.append(arguments) or 0,
        label_status_reader=lambda _label: pytest.fail("label status queried"),
    )

    # Then
    assert receipt.result == "waiting_authority"
    assert receipt.preparation == "not_prepared"
    assert receipt.activation == "not_activated"
    assert not plan_path.exists()
    assert calls == []


def test_tampered_receipt_blocks_before_launchctl(tmp_path: Path) -> None:
    # Given: an activated schedule whose activation receipt no longer binds its manifest.
    _request, plan, request_path, _plan_path = _authority_files(tmp_path)
    plan_path = tmp_path / "coordinator-plan.json"
    launchd = _LaunchdFixture()
    coordinator_request = FutureSessionCoordinatorRequest(
        request_path=request_path,
        plan_path=plan_path,
        launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
    )
    first = coordinate_future_session(
        coordinator_request,
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )
    assert first.activation_receipt is not None
    receipt_path = first.activation_receipt
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["manifest_sha256"] = "0" * 64
    receipt_path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt_path.chmod(0o600)
    launchd.calls.clear()

    # When
    blocked = coordinate_future_session(
        coordinator_request,
        launchctl_runner=launchd.run,
        label_status_reader=lambda _label: pytest.fail("label status queried"),
    )

    # Then
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    assert blocked.result == "blocked"
    assert blocked.reason == "activation_receipt_mismatch"
    assert launchd.calls == []


def test_partial_installed_set_blocks_before_launchctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: canonical preparation with only one destination already installed.
    _request, plan, request_path, _seeded_plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    plan_path = tmp_path / "coordinator-plan.json"
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    request = FutureSessionCoordinatorRequest(
        request_path=request_path,
        plan_path=plan_path,
        launch_agents_dir=launch_agents,
    )
    monkey_calls: list[tuple[str, ...]] = []

    def stop_after_prepare(*, manifest_path: Path, launch_agents_dir: Path, launchctl_runner):
        del manifest_path, launch_agents_dir, launchctl_runner
        raise RuntimeError("stop after prepare")

    monkeypatch.setattr(
        coordinator_module,
        "activate_us_future_session",
        stop_after_prepare,
    )
    with pytest.raises(RuntimeError, match="stop after prepare"):
        coordinate_future_session(request)
    monkeypatch.undo()
    manifest = json.loads((plan.artifact_layout.root / "preparation-manifest.json").read_text())
    entry = manifest["entries"][0]
    launch_agents.mkdir(mode=0o700, parents=True)
    destination = launch_agents / f"{entry['label']}.plist"
    destination.write_bytes(Path(entry["persistent_plist"]).read_bytes())
    destination.chmod(0o600)

    # When
    blocked = coordinate_future_session(
        request,
        launchctl_runner=lambda arguments: monkey_calls.append(arguments) or 0,
        label_status_reader=lambda _label: pytest.fail("label status queried"),
    )

    # Then
    assert blocked.result == "blocked"
    assert blocked.reason == "partial_installed_set"
    assert monkey_calls == []


def test_conflicting_manifest_blocks_before_launchctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a canonical preparation manifest that names a conflicting scheduler commit.
    _request, plan, request_path, _seeded_plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    coordinator_request = FutureSessionCoordinatorRequest(
        request_path=request_path,
        plan_path=tmp_path / "coordinator-plan.json",
        launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
    )

    def stop_after_prepare(*, manifest_path: Path, launch_agents_dir: Path, launchctl_runner):
        del manifest_path, launch_agents_dir, launchctl_runner
        raise RuntimeError("stop after prepare")

    monkeypatch.setattr(
        coordinator_module,
        "activate_us_future_session",
        stop_after_prepare,
    )
    with pytest.raises(RuntimeError, match="stop after prepare"):
        coordinate_future_session(coordinator_request)
    monkeypatch.undo()
    manifest_path = plan.artifact_layout.root / "preparation-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["scheduler_main_sha"] = "0" * 40
    manifest_path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    calls: list[tuple[str, ...]] = []

    # When
    blocked = coordinate_future_session(
        coordinator_request,
        launchctl_runner=lambda arguments: calls.append(arguments) or 0,
        label_status_reader=lambda _label: pytest.fail("label status queried"),
    )

    # Then
    assert blocked.result == "blocked"
    assert blocked.reason == "preparation_conflict"
    assert calls == []


def test_concurrent_coordinator_claim_blocks_before_launchctl(tmp_path: Path) -> None:
    # Given: another coordinator owns the exact artifact-root claim.
    _request, plan, request_path, _seeded_plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    claim = plan.artifact_layout.root.parent / f".{plan.artifact_layout.root.name}.coordinator.lock"
    claim.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    claim.write_bytes(b"")
    claim.chmod(0o600)
    descriptor = os.open(claim, os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    calls: list[tuple[str, ...]] = []

    # When
    try:
        blocked = coordinate_future_session(
            FutureSessionCoordinatorRequest(
                request_path=request_path,
                plan_path=tmp_path / "coordinator-plan.json",
                launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
            ),
            launchctl_runner=lambda arguments: calls.append(arguments) or 0,
            label_status_reader=lambda _label: pytest.fail("label status queried"),
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    # Then
    assert blocked.result == "blocked"
    assert blocked.reason == "concurrent_coordinator"
    assert calls == []
    assert claim.exists()


def test_unlocked_stable_claim_file_is_restart_safe(tmp_path: Path) -> None:
    # Given: a stable claim file remains after a previous coordinator process exited.
    _request, plan, request_path, _seeded_plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    claim = plan.artifact_layout.root.parent / f".{plan.artifact_layout.root.name}.coordinator.lock"
    claim.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    claim.write_bytes(b"")
    claim.chmod(0o600)
    launchd = _LaunchdFixture()

    # When
    receipt = coordinate_future_session(
        FutureSessionCoordinatorRequest(
            request_path=request_path,
            plan_path=tmp_path / "coordinator-plan.json",
            launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
        ),
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )

    # Then
    assert receipt.result == "activated"
    assert claim.exists()
