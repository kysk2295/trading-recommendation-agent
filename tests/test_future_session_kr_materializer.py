from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

import run_future_session_materialize
from tests.future_session_kr_support import kr_authority_files
from trading_agent.future_session_kr_activation import activate_kr_future_session
from trading_agent.future_session_kr_materializer import (
    materialize_kr_future_session,
)
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_kr_payload import (
    KrRestartableRunnerSpec,
    render_kr_restartable_runner,
)
from trading_agent.future_session_materialize_cli_parser import (
    build_future_session_parser,
)
from trading_agent.future_session_plan_models import (
    canonical_request_json,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)


def test_prepare_kr_command_is_exposed() -> None:
    # Given
    arguments = (
        "prepare-kr",
        "--request",
        "/tmp/request.json",
        "--plan",
        "/tmp/plan.json",
        "--output-dir",
        "/tmp/output",
    )

    # When
    parsed = build_future_session_parser().parse_args(arguments)

    # Then
    assert parsed.command == "prepare-kr"


def test_kr_restartable_wrapper_leaves_abnormal_exit_unreceipted(tmp_path: Path) -> None:
    # Given
    receipt = tmp_path / "receipt.json"
    plist = tmp_path / "job.plist"
    plist.write_text("fixture", encoding="utf-8")
    now = int(time.time())
    failed = tmp_path / "failed.zsh"
    failed.write_text(
        render_kr_restartable_runner(
            KrRestartableRunnerSpec(
                label="ai.trading-agent.fixture",
                run_epoch=now - 1,
                expires_epoch=now + 60,
                receipt=receipt,
                command=("/usr/bin/false",),
                persistent_plist=plist,
            )
        ),
        encoding="utf-8",
    )
    failed.chmod(0o700)

    # When
    failed_result = subprocess.run(("/bin/zsh", str(failed)), check=False)

    # Then
    assert failed_result.returncode == 1
    assert not receipt.exists()
    assert plist.exists()


def test_prepare_kr_materializes_exactly_one_restartable_supervisor(
    tmp_path: Path,
) -> None:
    # Given
    request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    ledger_before = request.experiment_ledger.read_bytes()
    launch_agents = tmp_path / "Library" / "LaunchAgents"

    # When
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )

    # Then
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["entry"]
    assert manifest["request_sha256"] == hashlib.sha256(canonical_request_json(request).encode()).hexdigest()
    assert manifest["plan_sha256"] == plan.plan_sha256
    assert manifest["scheduler_main_sha"] == request.scheduler_main_sha
    assert manifest["runtime_commit_sha"] == request.frozen_runtime.commit_sha
    assert manifest["experiment_ledger_schema_version"] == 7
    assert manifest["kr_rollover_bundle_sha256"] == plan.kr_rollover_bundle_sha256
    assert manifest["kr_policy_sha256"] == plan.kr_policy_sha256
    assert manifest["internal_phase_epochs"] == [int(job.run_at.timestamp()) for job in plan.jobs]
    assert request.experiment_ledger.read_bytes() == ledger_before
    assert len(tuple((plan.artifact_layout.root / "jobs").glob("*.plist"))) == 1
    assert len(tuple((plan.artifact_layout.root / "jobs").glob("*.persistent.zsh"))) == 1
    assert len(tuple((plan.artifact_layout.root / "jobs").glob("*.payload.zsh"))) == 1
    assert len(tuple((plan.artifact_layout.root / "receipts").glob("*.json"))) == 0
    assert "finalizer" not in "\n".join(str(path) for path in plan.artifact_layout.root.rglob("*"))
    with Path(entry["persistent_plist"]).open("rb") as handle:
        launch_agent = plistlib.load(handle)
    assert launch_agent["KeepAlive"] == {"SuccessfulExit": False}
    assert launch_agent["Label"].endswith(f".{plan.target_session.isoformat()}.supervisor")
    payload = Path(entry["payload_wrapper"]).read_text(encoding="utf-8")
    assert "supervise-kr" in payload
    assert "supervise-kr-preflight" not in payload
    wrapper = Path(entry["persistent_wrapper"]).read_text(encoding="utf-8")
    assert "if (( exit_code == 0 ))" in wrapper
    assert "write_receipt" in wrapper
    assert "readonly -a internal_phase_epochs=(" in payload
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600


def test_kr_activation_rejects_tamper_and_rolls_back_failed_bootstrap(
    tmp_path: Path,
) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = Path(manifest["entry"]["payload_wrapper"])
    payload.write_text(payload.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    payload.chmod(0o700)

    # When / Then
    with pytest.raises(FutureSessionActivationError, match="artifact_hash_mismatch"):
        activate_kr_future_session(
            manifest_path=manifest_path,
            launch_agents_dir=launch_agents,
            launchctl_runner=lambda _arguments: 113,
        )

    shutil.rmtree(plan.artifact_layout.root)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    calls: list[tuple[str, ...]] = []

    def launchctl(arguments: tuple[str, ...]) -> int:
        calls.append(arguments)
        return 113 if arguments[0] == "print" else 1

    with pytest.raises(FutureSessionActivationError, match="launchctl_bootstrap_failed"):
        activate_kr_future_session(
            manifest_path=manifest_path,
            launch_agents_dir=launch_agents,
            launchctl_runner=launchctl,
        )
    assert not tuple(launch_agents.glob("*.plist"))
    assert not (plan.artifact_layout.root / "activation-receipt.json").exists()


def test_kr_activation_installs_one_label_and_one_activation_receipt(
    tmp_path: Path,
) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    calls: list[tuple[str, ...]] = []

    def launchctl(arguments: tuple[str, ...]) -> int:
        calls.append(arguments)
        return 113 if arguments[0] == "print" else 0

    with pytest.raises(
        FutureSessionActivationError,
        match="launchctl_label_already_loaded",
    ):
        activate_kr_future_session(
            manifest_path=manifest_path,
            launch_agents_dir=launch_agents,
            launchctl_runner=lambda _arguments: 0,
        )

    # When
    activation = activate_kr_future_session(
        manifest_path=manifest_path,
        launch_agents_dir=launch_agents,
        launchctl_runner=launchctl,
    )

    # Then
    assert activation.installed_plist.is_file()
    assert tuple(call[0] for call in calls) == ("print", "bootstrap")
    receipt = json.loads(activation.receipt_path.read_text(encoding="utf-8"))
    assert receipt["label"] == activation.label
    assert receipt["result"] == "activated"
    assert len(tuple(plan.artifact_layout.root.glob("activation-receipt.json"))) == 1


def test_kr_activation_uses_frozen_authority_after_main_advances(
    tmp_path: Path,
) -> None:
    # Given: preparation is bound to a clean frozen runtime before mutable main advances.
    request, plan, request_path, plan_path = kr_authority_files(
        tmp_path,
        scheduler_authority_mode="frozen_runtime",
    )
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=launch_agents,
        )
    )
    authority = request.authority_repository
    (authority / "authority.txt").write_text("advanced\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(authority), "add", "authority.txt"), check=True)
    subprocess.run(
        ("git", "-C", str(authority), "commit", "--quiet", "-m", "advance main"),
        check=True,
    )

    # When: activation verifies the pinned scheduler authority.
    activation = activate_kr_future_session(
        manifest_path=manifest_path,
        launch_agents_dir=launch_agents,
        launchctl_runner=lambda arguments: 113 if arguments[0] == "print" else 0,
    )

    # Then: mutable main does not invalidate the frozen session authority.
    assert activation.installed_plist.is_file()
    assert activation.receipt_path.is_file()


def test_kr_cli_help_bad_prepare_happy_prepare_and_typed_preflight(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    help_text = build_future_session_parser().format_help()
    invalid_request = tmp_path / "invalid-request.json"
    invalid_plan = tmp_path / "invalid-plan.json"
    invalid_request.write_text("{\n", encoding="utf-8")
    invalid_plan.write_text("{\n", encoding="utf-8")
    invalid_request.chmod(0o600)
    invalid_plan.chmod(0o600)

    # When
    invalid_code = run_future_session_materialize.main(
        (
            "prepare-kr",
            "--request",
            str(invalid_request),
            "--plan",
            str(invalid_plan),
            "--output-dir",
            str((tmp_path / "invalid-output").absolute()),
        )
    )
    invalid_output = json.loads(capsys.readouterr().out)
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path / "happy")
    happy_code = run_future_session_materialize.main(
        (
            "prepare-kr",
            "--request",
            str(request_path),
            "--plan",
            str(plan_path),
            "--output-dir",
            str(plan.artifact_layout.root),
        )
    )
    happy_output = json.loads(capsys.readouterr().out)
    preflight_code = run_future_session_materialize.main(
        (
            "supervise-kr-preflight",
            "--manifest",
            happy_output["manifest"],
        )
    )
    preflight_output = json.loads(capsys.readouterr().out)

    # Then
    assert "prepare-kr" in help_text
    assert "activate-kr" in help_text
    assert invalid_code == 2
    assert invalid_output == {"result": "invalid_materialization_authority"}
    assert happy_code == 0
    assert happy_output["result"] == "prepared"
    assert preflight_code == 0
    assert preflight_output == {
        "lifecycle_completion": False,
        "result": "ready_to_prepare",
        "session_execution": False,
    }
