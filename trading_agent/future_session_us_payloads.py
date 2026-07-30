from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.future_session_plan_models import (
    FinalizerReadinessGate,
    FutureSessionPayloadMode,
    FutureSessionPlanRequest,
    FutureSessionUsRole,
    JobTimingSpec,
    RuntimeEnvironmentAttestation,
)

_NY = ZoneInfo("America/New_York")
_WATCH_DATABASE_NAME = "paper_recommendations.sqlite3"


@dataclass(frozen=True, slots=True)
class InvalidUsFutureSessionPayloadError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def attest_us_runtime(
    request: FutureSessionPlanRequest,
) -> tuple[RuntimeEnvironmentAttestation | None, str | None]:
    interpreter = request.runtime_interpreter
    if interpreter is None or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        return None, "runtime_environment_invalid"
    script = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "try:\n"
        " import duckdb\n"
        " from trading_agent.execution_store import ExecutionStore\n"
        " from trading_agent.experiment_ledger_store import ExperimentLedgerReader\n"
        " from trading_agent.lane_registry_store import LaneRegistryReader\n"
        " stores=(ExperimentLedgerReader(Path(sys.argv[1])).is_initialized()"
        " and LaneRegistryReader(Path(sys.argv[2])).is_initialized()"
        " and ExecutionStore(Path(sys.argv[3])).is_initialized())\n"
        " print(json.dumps({'duckdb':duckdb.__version__,'python':sys.version.split()[0],'stores':stores}))\n"
        "except Exception as error:\n"
        " print(json.dumps({'error':type(error).__name__}))\n"
    )
    environment = os.environ.copy()
    runtime = request.frozen_runtime.directory
    import_root = runtime if (runtime / "trading_agent").is_dir() else Path(__file__).parents[1]
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(import_root)
        if existing_pythonpath is None
        else os.pathsep.join((str(import_root), existing_pythonpath))
    )
    try:
        completed = subprocess.run(
            (
                str(interpreter),
                "-c",
                script,
                str(request.experiment_ledger),
                str(request.lane_registry),
                str(request.execution_database),
            ),
            cwd=runtime,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(completed.stdout)
        if completed.returncode != 0 or "error" in payload:
            return None, "runtime_environment_invalid"
        if payload.get("stores") is not True:
            return None, "frozen_runtime_store_schema_incompatible"
        content = {
            "duckdb_version": str(payload["duckdb"]),
            "interpreter": str(interpreter),
            "python_version": str(payload["python"]),
            "runtime_commit": request.frozen_runtime.commit_sha,
        }
        digest = hashlib.sha256(
            json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        return RuntimeEnvironmentAttestation(
            interpreter=interpreter,
            python_version=content["python_version"],
            duckdb_version=content["duckdb_version"],
            attestation_sha256=digest,
        ), None
    except (OSError, KeyError, TypeError, ValidationError, ValueError, subprocess.SubprocessError):
        return None, "runtime_environment_invalid"


def build_us_jobs(
    request: FutureSessionPlanRequest,
    target: dt.date,
    orb_strategy_version: str,
) -> tuple[JobTimingSpec, ...]:
    root = request.artifact_root / "us" / target.isoformat()
    runtime = request.frozen_runtime.directory.resolve(strict=False)
    interpreter = _required(request.runtime_interpreter)
    requested_watch_database = _required(request.watch_database)
    watch_database = requested_watch_database.resolve(strict=False)
    if watch_database != requested_watch_database:
        raise InvalidUsFutureSessionPayloadError(
            "noncanonical_watch_database"
        )
    try:
        watch_source = watch_database.relative_to(runtime)
    except ValueError:
        raise InvalidUsFutureSessionPayloadError(
            "watch_database_outside_frozen_runtime"
        ) from None
    if (
        watch_database.name != _WATCH_DATABASE_NAME
        or not watch_source.parts
        or watch_source.parts[0] != "outputs"
    ):
        raise InvalidUsFutureSessionPayloadError(
            "noncanonical_watch_database"
        )
    common_run = dt.datetime.combine(target, dt.time(8), tzinfo=_NY)
    common_expiry = dt.datetime.combine(target, dt.time(16, 20), tzinfo=_NY)
    session_id = f"XNYS-{target.isoformat()}"
    watcher = JobTimingSpec(
        job_id=f"us-orb-watcher-{target}",
        role=FutureSessionUsRole.US_ORB_WATCHER,
        label=f"ai.trading-agent.us-orb-watcher-{target:%Y%m%d}",
        run_at=common_run,
        expires_at=common_expiry,
        purpose="watch_open=09:30;finalize=16:05",
        command=(
            str(interpreter), str(runtime / "run_kis_paper_watch.py"),
            "--output-dir", str(watch_database.parent),
            "--cycles", str(request.cycles),
            "--interval-seconds", str(request.interval_seconds),
            "--max-wait-minutes", "720",
            "--top", "10",
            "--max-pages", "1",
            "--collect-premarket",
            "--premarket-interval-seconds", "300",
            "--wait-until-open", "--strategy", "orb",
            "--lane-execution-database", str(request.execution_database),
            "--lane-registry", str(request.lane_registry),
            "--lane-review-ledger", str(request.lane_review_ledger),
            "--lane-forward-output-dir", str(root / "lane-forward"),
            "--experiment-ledger", str(request.experiment_ledger),
            "--delivery-database", str(request.delivery_database),
        ),
        source_paths=(
            request.experiment_ledger,
            _required(request.lane_registry),
            _required(request.lane_review_ledger),
        ),
        destination_paths=(watch_database,),
    )
    projection = JobTimingSpec(
        job_id=f"us-hermes-projection-{target}",
        role=FutureSessionUsRole.US_HERMES_PROJECTION,
        label=f"ai.trading-agent.us-hermes-projection-{target:%Y%m%d}",
        run_at=common_run,
        expires_at=common_expiry,
        purpose="poll_outboxes_until=16:15",
        command=(
            str(interpreter), str(runtime / "run_hermes_delivery.py"), "project-session",
            "--database", str(request.delivery_database), "--opportunities",
            str(request.opportunity_outbox), "--signals", str(request.signal_outbox),
            "--session-date", target.isoformat(),
        ),
        dependencies=(FutureSessionUsRole.US_ORB_WATCHER,),
        source_paths=(_required(request.opportunity_outbox), _required(request.signal_outbox)),
        destination_paths=(_required(request.delivery_database),),
        payload_mode=FutureSessionPayloadMode.REPEAT_THROUGH_DEADLINE,
        poll_until=dt.datetime.combine(target, dt.time(16, 15), tzinfo=_NY),
        poll_interval_seconds=5,
    )
    preflight = _observer_job(
        request, target, common_run, dt.time(15, 35),
        FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER,
        "preflight",
        (
            "preflight",
            "--execution-database",
            str(request.execution_database),
            "--watch-database",
            str(request.watch_database),
        ),
        "poll_watch_database;cutoff=15:30",
    )
    finalizer = _observer_job(
        request, target, common_run, dt.time(16, 20),
        FutureSessionUsRole.US_DAY_CLOSE_FINALIZER,
        "close-finalizer",
        (
            "finalize",
            "--delivery-database",
            str(request.delivery_database),
            "--execution-database",
            str(request.execution_database),
            "--repository",
            str(runtime),
            "--session-id",
            session_id,
            "--strategy-version",
            orb_strategy_version,
            "--source-artifact",
            str(watch_source),
            "--terminal-output",
            str(root / "terminal.json"),
        ),
        "wait_watcher_stable;finalize=16:05;source_deadline=16:15",
        (FutureSessionUsRole.US_ORB_WATCHER, FutureSessionUsRole.US_HERMES_PROJECTION),
    )
    arm = JobTimingSpec(
        job_id=f"us-day-arm-observer-{target}",
        role=FutureSessionUsRole.US_DAY_ARM_OBSERVER,
        label=f"ai.trading-agent.us-day-arm-observer-{target:%Y%m%d}",
        run_at=dt.datetime.combine(target, dt.time(9), tzinfo=_NY),
        expires_at=dt.datetime.combine(target, dt.time(15, 31), tzinfo=_NY),
        purpose="open=09:30;entry_cutoff=15:30",
        command=(
            str(interpreter), str(runtime / "run_us_day_armed_entry.py"),
            "--arm-database", str(request.arm_database), "--delivery-database", str(request.delivery_database),
            "--execution-database", str(request.execution_database), "--watch-database", str(request.watch_database),
            "--experiment-ledger", str(request.experiment_ledger), "--lane-registry", str(request.lane_registry),
            "--repository", str(runtime), "--signing-key", str(request.signing_key),
            "--session-id", session_id, "--entry-cutoff", f"{target}T15:30:00-04:00",
            "--poll-interval-seconds", "5",
        ),
        dependencies=(FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER,),
        source_paths=(_required(request.signing_key), _required(request.watch_database)),
        destination_paths=(_required(request.arm_database), _required(request.execution_database)),
    )
    return (watcher, projection, preflight, finalizer, arm)


def _observer_job(
    request: FutureSessionPlanRequest,
    target: dt.date,
    run_at: dt.datetime,
    expiry: dt.time,
    role: FutureSessionUsRole,
    name: str,
    arguments: tuple[str, ...],
    purpose: str,
    dependencies: tuple[FutureSessionUsRole, ...] = (),
) -> JobTimingSpec:
    match role:
        case FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER:
            not_before = None
            poll_until = dt.datetime.combine(
                target,
                dt.time(15, 30),
                tzinfo=_NY,
            )
        case FutureSessionUsRole.US_DAY_CLOSE_FINALIZER:
            not_before = dt.datetime.combine(
                target,
                dt.time(16, 5),
                tzinfo=_NY,
            )
            poll_until = dt.datetime.combine(
                target,
                dt.time(16, 15),
                tzinfo=_NY,
            )
        case _:
            raise InvalidUsFutureSessionPayloadError(
                "invalid_observer_role"
            )
    return JobTimingSpec(
        job_id=f"us-day-{name}-{target}",
        role=role,
        label=f"ai.trading-agent.us-day-{name}-{target:%Y%m%d}",
        run_at=run_at,
        expires_at=dt.datetime.combine(target, expiry, tzinfo=_NY),
        purpose=purpose,
        command=(
            str(_required(request.runtime_interpreter)),
            str(
                request.frozen_runtime.directory
                / "run_us_day_operating_session.py"
            ),
            *arguments,
        ),
        dependencies=dependencies,
        source_paths=(request.experiment_ledger,),
        destination_paths=(_required(request.execution_database),),
        payload_mode=FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS,
        not_before=not_before,
        poll_until=poll_until,
        poll_interval_seconds=5,
        finalizer_gate=(
            None
            if role is FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER
            else _finalizer_gate(request, target)
        ),
    )


def _finalizer_gate(
    request: FutureSessionPlanRequest,
    target: dt.date,
) -> FinalizerReadinessGate:
    watcher_label = f"ai.trading-agent.us-orb-watcher-{target:%Y%m%d}"
    return FinalizerReadinessGate(
        watcher_label=watcher_label,
        watcher_active_probe=(
            "/bin/launchctl",
            "print",
            f"gui/{os.getuid()}/{watcher_label}",
        ),
        source_path=_required(request.watch_database),
        stability_seconds=5,
    )


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError
    return value


__all__ = (
    "InvalidUsFutureSessionPayloadError",
    "attest_us_runtime",
    "build_us_jobs",
)
