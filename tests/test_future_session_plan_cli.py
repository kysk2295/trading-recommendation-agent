from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import run_future_session_plan
from tests.test_forward_runtime_readiness_cli import _runtime, _stores
from tests.test_future_session_plan_compiler import _us_request
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    canonical_request_json,
)

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_future_session_plan.py"


def test_cli_help_is_available() -> None:
    # Given / When
    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    assert "compile" in completed.stdout


def test_cli_rejects_malformed_request_without_path_disclosure(
    tmp_path: Path,
    capsys,
) -> None:
    # Given
    request = tmp_path / "request.json"
    request.write_text("{", encoding="utf-8")
    request.chmod(0o600)

    # When
    exit_code = run_future_session_plan.main(
        ("compile", "--request", str(request.absolute()))
    )

    # Then
    assert exit_code == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"result": "invalid_request"}
    assert str(request) not in output


def test_cli_reports_waiting_as_typed_success(
    tmp_path: Path,
    capsys,
) -> None:
    # Given
    request = tmp_path / "request.json"
    model = FutureSessionPlanRequest(
        market=FutureSessionMarket.US,
        after_date=dt.date(2026, 7, 24),
        compiled_at=dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        scheduler_main_sha="b" * 40,
        authority_repository=(tmp_path / "missing-authority").absolute(),
        frozen_runtime=FrozenRuntimeAuthority(
            directory=(tmp_path / "missing-runtime").absolute(),
            commit_sha="a" * 40,
        ),
        artifact_root=(tmp_path / "artifacts").absolute(),
        experiment_ledger=(tmp_path / "missing-experiment.sqlite3").absolute(),
        lane_registry=(tmp_path / "missing-lane.sqlite3").absolute(),
        execution_database=(tmp_path / "missing-execution.sqlite3").absolute(),
        runtime_interpreter=Path("/usr/bin/python3"),
        watch_database=(tmp_path / "watch.sqlite3").absolute(),
        delivery_database=(tmp_path / "delivery.sqlite3").absolute(),
        arm_database=(tmp_path / "arm.sqlite3").absolute(),
        signing_key=(tmp_path / "signing.env").absolute(),
        opportunity_outbox=(tmp_path / "opportunities.sqlite3").absolute(),
        signal_outbox=(tmp_path / "signals.sqlite3").absolute(),
        lane_review_ledger=(tmp_path / "lane-review.sqlite3").absolute(),
    )
    request.write_text(canonical_request_json(model), encoding="utf-8")
    request.chmod(0o600)

    # When
    exit_code = run_future_session_plan.main(
        ("compile", "--request", str(request.absolute()))
    )

    # Then
    decision = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert decision["status"] == "waiting_authority"
    assert decision["jobs"] == []
    assert stat.S_IMODE(request.stat().st_mode) == 0o600
    assert not (tmp_path / "artifacts").exists()


def test_cli_reports_local_ready_plan_without_materializing_jobs(
    tmp_path: Path,
    capsys,
) -> None:
    # Given
    runtime, required, head = _runtime(tmp_path)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    plan_request = _us_request(
        tmp_path,
        runtime=runtime,
        head=head,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    )
    request = tmp_path / "ready-request.json"
    request.write_text(
        json.dumps(
            plan_request.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    request.chmod(0o600)

    # When
    exit_code = run_future_session_plan.main(
        ("compile", "--request", str(request.absolute()))
    )

    # Then
    decision = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert decision["status"] == "ready_to_prepare"
    assert len(decision["jobs"]) == 5
    assert len(decision["strategy_registrations"]) == 4
    assert not (tmp_path / "artifacts").exists()
