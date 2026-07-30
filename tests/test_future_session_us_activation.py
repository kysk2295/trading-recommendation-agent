from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

import run_future_session_materialize
import trading_agent.future_session_us_activation as future_session_us_activation
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_plan_models import (
    FutureSessionUsRole,
    ReadyToPrepareSessionPlan,
)
from trading_agent.future_session_us_activation import (
    FutureSessionActivationError,
    activate_us_future_session,
    copy_private_file,
)
from trading_agent.future_session_us_materializer import materialize_us_future_session
from trading_agent.future_session_us_materializer_models import (
    UsFutureSessionMaterializationRequest,
)


def test_activate_installs_exact_five_private_launch_agents_and_writes_receipt(
    tmp_path: Path,
) -> None:
    # Given: a prepared canonical manifest and no loaded launchd labels.
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    _request, plan, request_path, plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    manifest_path = materialize_us_future_session(
        UsFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    source_hashes = {
        entry["role"]: hashlib.sha256(Path(entry["persistent_plist"]).read_bytes()).hexdigest()
        for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["entries"]
    }
    calls: list[tuple[str, ...]] = []

    def launchctl(arguments: tuple[str, ...]) -> int:
        calls.append(arguments)
        return 113 if arguments[0] == "print" else 0

    # When
    activation = activate_us_future_session(
        manifest_path=manifest_path,
        launch_agents_dir=launch_agents,
        launchctl_runner=launchctl,
    )

    # Then: all five immutable source artifacts bind one private activation receipt.
    receipt = json.loads(activation.receipt_path.read_text(encoding="utf-8"))
    assert receipt == {
        "labels": [entry.label for entry in activation.entries],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "result": "activated",
        "schema_version": 2,
    }
    assert stat.S_IMODE(activation.receipt_path.stat().st_mode) == 0o600
    assert len(activation.entries) == len(FutureSessionUsRole)
    assert tuple(call[0] for call in calls).count("print") == len(FutureSessionUsRole)
    assert tuple(call[0] for call in calls).count("bootstrap") == len(FutureSessionUsRole)
    for entry in activation.entries:
        assert entry.installed_plist == launch_agents / f"{entry.label}.plist"
        assert stat.S_IMODE(entry.installed_plist.stat().st_mode) == 0o600
        assert source_hashes[entry.role.value] == hashlib.sha256(entry.source_plist.read_bytes()).hexdigest()


def test_activate_cli_reports_typed_bad_input_and_canonical_happy_receipt(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    # Given: a private manifest path and an injected activation boundary.
    invalid_manifest = tmp_path / "invalid-manifest.json"
    invalid_manifest.write_text("{}\n", encoding="utf-8")
    invalid_manifest.chmod(0o600)
    receipt = tmp_path / "activation-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    receipt.chmod(0o600)
    expected_labels = tuple(role.value for role in FutureSessionUsRole)

    def activate(*, manifest_path: Path, launch_agents_dir: Path | None = None):
        assert manifest_path == invalid_manifest
        assert launch_agents_dir is None
        return future_session_us_activation.FutureSessionActivation(
            entries=tuple(
                future_session_us_activation.ActivatedUsRoleArtifact(
                    role=role,
                    label=role.value,
                    source_plist=tmp_path / "source" / f"{role.value}.plist",
                    installed_plist=tmp_path / "Library" / "LaunchAgents" / f"{role.value}.plist",
                )
                for role in FutureSessionUsRole
            ),
            receipt_path=receipt,
        )

    help_result = subprocess.run(
        (
            str(Path(__file__).parents[1] / ".venv" / "bin" / "python"),
            str(Path(__file__).parents[1] / "run_future_session_materialize.py"),
            "--help",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    # When
    invalid_code = run_future_session_materialize.main(("activate", "--manifest", str(invalid_manifest)))
    invalid_output = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(run_future_session_materialize, "activate_us_future_session", activate)
    happy_code = run_future_session_materialize.main(("activate", "--manifest", str(invalid_manifest)))
    happy_output = json.loads(capsys.readouterr().out)

    # Then: the CLI exposes activation and preserves its typed receipt surface.
    assert help_result.returncode == 0
    assert "activate" in help_result.stdout
    assert invalid_code == 2
    assert invalid_output == {"reason": "invalid_manifest", "result": "blocked"}
    assert happy_code == 0
    assert happy_output == {
        "labels": list(expected_labels),
        "receipt": str(receipt),
        "result": "activated",
    }


def test_activation_rolls_back_bootstrapped_and_installed_plists_on_failure(
    tmp_path: Path,
) -> None:
    # Given: the second launchd bootstrap fails after private plists are installed.
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    _, plan, request_path, plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    manifest_path = materialize_us_future_session(
        UsFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    calls: list[tuple[str, ...]] = []
    bootstrap_count = 0

    def launchctl(arguments: tuple[str, ...]) -> int:
        nonlocal bootstrap_count
        calls.append(arguments)
        if arguments[0] == "print":
            return 113
        if arguments[0] == "bootstrap":
            bootstrap_count += 1
            return 1 if bootstrap_count == 2 else 0
        return 0

    # When / Then: rollback removes every local install and loaded job.
    with pytest.raises(FutureSessionActivationError, match="launchctl_bootstrap_failed"):
        activate_us_future_session(
            manifest_path=manifest_path,
            launch_agents_dir=launch_agents,
            launchctl_runner=launchctl,
        )
    assert not tuple(launch_agents.glob("*.plist"))
    assert tuple(call[0] for call in calls).count("bootout") == 1
    assert not (plan.artifact_layout.root / "activation-receipt.json").exists()


def test_private_plist_publish_never_overwrites_a_raced_destination(tmp_path: Path) -> None:
    # Given: another process has won the destination pathname after activation preflight.
    source = tmp_path / "source.plist"
    destination = tmp_path / "LaunchAgents" / "ai.trading-agent.raced.plist"
    source.write_bytes(b"prepared")
    source.chmod(0o600)
    destination.parent.mkdir(mode=0o700)
    destination.write_bytes(b"raced")
    destination.chmod(0o600)

    # When / Then: the prepared plist cannot replace the raced destination.
    with pytest.raises(FileExistsError):
        copy_private_file(source, destination)
    assert destination.read_bytes() == b"raced"


def test_activation_keeps_a_receipt_created_during_publish_race(tmp_path: Path) -> None:
    # Given: the activation receipt path is claimed only after all plists bootstrap.
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    _, plan, request_path, plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    manifest_path = materialize_us_future_session(
        UsFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    receipt_path = plan.artifact_layout.root / "activation-receipt.json"
    bootstrap_count = 0

    def launchctl(arguments: tuple[str, ...]) -> int:
        nonlocal bootstrap_count
        if arguments[0] == "print":
            return 113
        if arguments[0] == "bootstrap":
            bootstrap_count += 1
            if bootstrap_count == len(FutureSessionUsRole):
                receipt_path.write_bytes(b"raced-receipt")
                receipt_path.chmod(0o600)
        return 0

    # When / Then: rollback removes only its jobs and preserves the raced receipt.
    with pytest.raises(OSError):
        activate_us_future_session(
            manifest_path=manifest_path,
            launch_agents_dir=launch_agents,
            launchctl_runner=launchctl,
        )
    assert receipt_path.read_bytes() == b"raced-receipt"
    assert not tuple(launch_agents.glob("*.plist"))
