from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Literal

from tests.test_forward_runtime_readiness_cli import _git, _runtime
from tests.test_future_session_plan_compiler import _kr_request
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    canonical_plan_json,
    canonical_request_json,
)


def kr_authority_files(
    tmp_path: Path,
    *,
    scheduler_authority_mode: Literal["current_main", "frozen_runtime"] = "current_main",
) -> tuple[FutureSessionPlanRequest, ReadyToPrepareSessionPlan, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime: Path | None = None
    if scheduler_authority_mode == "frozen_runtime":
        runtime, _required, _head = _runtime(tmp_path)
        shutil.copy2(
            Path(__file__).parents[1] / "run_future_session_materialize.py",
            runtime / "run_future_session_materialize.py",
        )
        publisher = runtime / "run_future_session_execution_incident_publisher.py"
        shutil.copy2(
            Path(__file__).parents[1] / publisher.name,
            publisher,
        )
        _git(
            runtime,
            "add",
            "run_future_session_materialize.py",
            str(publisher.relative_to(runtime)),
        )
        _git(runtime, "commit", "--quiet", "-m", "KR frozen supervisor entrypoint")
    request, _ledger, _day = _kr_request(tmp_path, runtime=runtime)
    authority = request.authority_repository
    if scheduler_authority_mode == "current_main":
        shutil.copy2(
            Path(__file__).parents[1] / "run_future_session_materialize.py",
            authority / "run_future_session_materialize.py",
        )
        _git(authority, "add", "run_future_session_materialize.py")
        _git(authority, "commit", "--quiet", "-m", "KR supervisor entrypoint")
        scheduler_sha = _git(authority, "rev-parse", "HEAD")
        _git(authority, "update-ref", "refs/remotes/origin/main", scheduler_sha)
    else:
        assert runtime is not None
        scheduler_sha = _git(runtime, "rev-parse", "HEAD")
    bound_request = request.model_copy(
        update={
            "scheduler_main_sha": scheduler_sha,
            "scheduler_authority_mode": scheduler_authority_mode,
            "runtime_interpreter": Path(sys.executable).absolute(),
            "delivery_database": (tmp_path / "delivery.sqlite3").absolute(),
        }
    )
    plan = compile_future_session_plan(bound_request)
    assert isinstance(plan, ReadyToPrepareSessionPlan)
    request_path = tmp_path / "kr-request.json"
    plan_path = tmp_path / "kr-plan.json"
    request_path.write_text(canonical_request_json(bound_request), encoding="utf-8")
    plan_path.write_text(canonical_plan_json(plan), encoding="utf-8")
    request_path.chmod(0o600)
    plan_path.chmod(0o600)
    return bound_request, plan, request_path, plan_path


__all__ = ("kr_authority_files",)
