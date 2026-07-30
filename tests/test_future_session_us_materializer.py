from __future__ import annotations

import hashlib
import json
import plistlib
import stat
import subprocess
from pathlib import Path
from typing import assert_never

import pytest

import run_future_session_materialize
import trading_agent.launchd_one_shot as launchd_one_shot
from tests.test_forward_runtime_readiness_cli import _runtime, _stores
from tests.test_future_session_plan_compiler import _us_request
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionUsRole,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_plan_json,
    canonical_request_json,
)
from trading_agent.future_session_us_materializer import (
    FutureSessionMaterializationError,
    materialize_us_future_session,
)


def test_prepare_atomically_materializes_exact_five_us_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given
    activated = False

    def reject_activation(_request) -> None:
        nonlocal activated
        activated = True
        raise AssertionError

    monkeypatch.setattr(
        launchd_one_shot,
        "submit_one_shot",
        reject_activation,
    )
    request, plan, request_path, plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    output = plan.artifact_layout.root

    # When
    manifest_path = materialize_us_future_session(
        request_path=request_path,
        plan_path=plan_path,
        output_dir=output,
    )

    # Then
    manifest_payload = manifest_path.read_bytes()
    manifest = json.loads(manifest_payload)
    assert tuple(entry["role"] for entry in manifest["entries"]) == tuple(
        role.value for role in FutureSessionUsRole
    )
    assert manifest["request_sha256"] == hashlib.sha256(
        canonical_request_json(request).encode()
    ).hexdigest()
    assert manifest["plan_sha256"] == plan.plan_sha256
    assert manifest["runtime_commit_sha"] == request.frozen_runtime.commit_sha
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert not tuple(output.parent.glob(f".{output.name}.prepare-*"))
    assert not activated
    signing_key = request.signing_key
    watch_database = request.watch_database
    opportunity_outbox = request.opportunity_outbox
    signal_outbox = request.signal_outbox
    assert signing_key is not None and not signing_key.exists()
    assert watch_database is not None and not watch_database.exists()
    assert opportunity_outbox is not None and not opportunity_outbox.exists()
    assert signal_outbox is not None and not signal_outbox.exists()
    jobs_by_role = {
        job.role.value: job for job in plan.jobs if job.role is not None
    }
    for entry in manifest["entries"]:
        payload = Path(entry["payload_wrapper"])
        wrapper = Path(entry["persistent_wrapper"])
        plist = Path(entry["persistent_plist"])
        assert stat.S_IMODE(payload.stat().st_mode) == 0o700
        assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
        assert stat.S_IMODE(plist.stat().st_mode) == 0o600
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == entry["payload_sha256"]
        assert (
            hashlib.sha256(wrapper.read_bytes()).hexdigest()
            == entry["persistent_wrapper_sha256"]
        )
        assert subprocess.run(
            ("/bin/zsh", "-n", str(payload)),
            check=False,
            capture_output=True,
            text=True,
        ).returncode == 0
        assert subprocess.run(
            ("/bin/zsh", "-n", str(wrapper)),
            check=False,
            capture_output=True,
            text=True,
        ).returncode == 0
        with plist.open("rb") as handle:
            launch_agent = plistlib.load(handle)
        assert launch_agent["Label"] == entry["label"]
        wrapper_text = wrapper.read_text(encoding="utf-8")
        assert '"schema_version":2' in wrapper_text
        assert plan.plan_sha256 in wrapper_text
        assert request.frozen_runtime.commit_sha in wrapper_text
        assert str(manifest_path) in wrapper_text
        payload_text = payload.read_text(encoding="utf-8")
        job = jobs_by_role[entry["role"]]
        if job.poll_until is not None:
            assert (
                f"readonly poll_deadline_epoch={int(job.poll_until.timestamp())}"
                in payload_text
            )
            assert (
                f"readonly poll_interval_seconds={job.poll_interval_seconds}"
                in payload_text
            )
        match job.role:
            case FutureSessionUsRole.US_HERMES_PROJECTION:
                assert (
                    'source_signature="${opportunity_stat}|${signal_stat}"'
                    in payload_text
                )
                assert (
                    "[[ $source_signature != $projected_signature ]]"
                    in payload_text
                )
                assert "pending_exit_code=0" in payload_text
                assert "if (( pending_exit_code != 0 )); then" in payload_text
                assert "exit $pending_exit_code" in payload_text
                assert "exit 0" in payload_text
                assert "deadline_elapsed" not in payload_text
            case FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER:
                assert (
                    '{"reason":"no_ready_current_setup","result":"censored"}'
                    in payload_text
                )
                assert "if [[ $watch_stat != $observed_signature ]]" in payload_text
                assert "exit 0" in payload_text
                assert "deadline_elapsed" not in payload_text
            case FutureSessionUsRole.US_DAY_CLOSE_FINALIZER:
                assert (
                    'print -u2 -r -- "{\\"reason\\":\\"$1\\",'
                    '\\"result\\":\\"blocked\\"}"'
                    in payload_text
                )
                assert "if (( now_epoch >= poll_deadline_epoch )); then" in payload_text
                assert "exit 78" in payload_text
                assert "watcher_active" in payload_text
                assert "watch_source_missing" in payload_text
                assert "watch_source_unstable" in payload_text
                assert "deadline_elapsed" not in payload_text
            case (
                FutureSessionUsRole.US_ORB_WATCHER
                | FutureSessionUsRole.US_DAY_ARM_OBSERVER
            ):
                assert job.poll_until is None
            case None:
                raise AssertionError
            case unreachable:
                assert_never(unreachable)
        if job.not_before is not None:
            assert (
                f"readonly not_before_epoch={int(job.not_before.timestamp())}"
                in payload_text
            )


def test_invalid_plan_leaves_no_partial_output(tmp_path: Path) -> None:
    # Given
    _, plan, request_path, plan_path = _authority_files(tmp_path)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["scheduler_main_sha"] = "f" * 40
    plan_path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    plan_path.chmod(0o600)

    # When / Then
    with pytest.raises(FutureSessionMaterializationError):
        materialize_us_future_session(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
        )
    assert not plan.artifact_layout.root.exists()
    assert not tuple(
        plan.artifact_layout.root.parent.glob(
            f".{plan.artifact_layout.root.name}.prepare-*"
        )
    )


def test_prepare_cli_help_bad_input_and_happy_path(
    tmp_path: Path,
    capsys,
) -> None:
    # Given
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
    invalid_request = tmp_path / "invalid-request.json"
    invalid_plan = tmp_path / "invalid-plan.json"
    invalid_request.write_text("{", encoding="utf-8")
    invalid_plan.write_text("{", encoding="utf-8")
    invalid_request.chmod(0o600)
    invalid_plan.chmod(0o600)

    # When
    invalid_code = run_future_session_materialize.main(
        (
            "prepare",
            "--request",
            str(invalid_request),
            "--plan",
            str(invalid_plan),
            "--output-dir",
            str((tmp_path / "invalid-output").absolute()),
        )
    )
    invalid_output = json.loads(capsys.readouterr().out)
    happy_root = tmp_path / "happy"
    happy_root.mkdir()
    request, plan, request_path, plan_path = _authority_files(happy_root)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    waiting_request = request.model_copy(
        update={
            "authority_repository": (tmp_path / "missing-authority").absolute(),
            "scheduler_main_sha": "f" * 40,
        }
    )
    waiting_plan = compile_future_session_plan(waiting_request)
    assert isinstance(waiting_plan, WaitingSessionAuthority)
    waiting_request_path = tmp_path / "waiting-request.json"
    waiting_plan_path = tmp_path / "waiting-plan.json"
    waiting_request_path.write_text(
        canonical_request_json(waiting_request),
        encoding="utf-8",
    )
    waiting_plan_path.write_text(
        canonical_plan_json(waiting_plan),
        encoding="utf-8",
    )
    waiting_request_path.chmod(0o600)
    waiting_plan_path.chmod(0o600)
    waiting_code = run_future_session_materialize.main(
        (
            "prepare",
            "--request",
            str(waiting_request_path),
            "--plan",
            str(waiting_plan_path),
            "--output-dir",
            str((tmp_path / "waiting-output").absolute()),
        )
    )
    waiting_output = json.loads(capsys.readouterr().out)
    happy_code = run_future_session_materialize.main(
        (
            "prepare",
            "--request",
            str(request_path),
            "--plan",
            str(plan_path),
            "--output-dir",
            str(plan.artifact_layout.root),
        )
    )
    happy_output = json.loads(capsys.readouterr().out)

    # Then
    assert help_result.returncode == 0
    assert "prepare" in help_result.stdout
    assert invalid_code == 2
    assert invalid_output == {"result": "invalid_materialization_authority"}
    assert not (tmp_path / "invalid-output").exists()
    assert waiting_code == 2
    assert waiting_output == {"result": "invalid_materialization_authority"}
    assert not (tmp_path / "waiting-output").exists()
    assert happy_code == 0
    assert happy_output == {
        "manifest": str(plan.artifact_layout.root / "preparation-manifest.json"),
        "result": "prepared",
    }


def _authority_files(
    tmp_path: Path,
):
    runtime, required, head = _runtime(tmp_path)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    request = _us_request(
        tmp_path,
        runtime=runtime,
        head=head,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    )
    plan = compile_future_session_plan(request)
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    request_path.write_text(canonical_request_json(request), encoding="utf-8")
    plan_path.write_text(canonical_plan_json(plan), encoding="utf-8")
    request_path.chmod(0o600)
    plan_path.chmod(0o600)
    return request, plan, request_path, plan_path
