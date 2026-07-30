from __future__ import annotations

import json
import subprocess
from pathlib import Path

import run_future_session_materialize
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_plan_json,
    canonical_request_json,
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
