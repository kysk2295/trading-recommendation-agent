from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from trading_agent.future_session_kr_ledger_identity import (
    experiment_ledger_v7_identity,
)
from trading_agent.future_session_kr_manifest import (
    KrFutureSessionPreparationManifest,
    PreparedKrSupervisorArtifact,
    canonical_kr_manifest_json,
)
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_kr_payload import (
    KrRestartableRunnerSpec,
    KrSupervisorPayloadSpec,
    render_kr_restartable_runner,
    render_kr_supervisor_payload,
)
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    canonical_plan_json,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_verifier import verify_private_directory
from trading_agent.future_session_us_materializer_errors import (
    FutureSessionMaterializationError,
)
from trading_agent.future_session_us_materializer_io import (
    sha256,
    stage_path,
    write_private_file,
)
from trading_agent.future_session_us_materializer_reader import (
    read_private_canonical_file,
)

_PLAN_ADAPTER = TypeAdapter(FutureSessionPlanDecision)
_FILE_MODE = 0o600
_EXECUTABLE_MODE = 0o700
_DIRECTORY_MODE = 0o700


def materialize_kr_future_session(
    materialization: KrFutureSessionMaterializationRequest,
) -> Path:
    output = materialization.output_dir
    launch_agents = materialization.launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    if not output.is_absolute() or not launch_agents.is_absolute():
        raise FutureSessionMaterializationError("absolute_output_required")
    request_payload = read_private_canonical_file(materialization.request_path)
    plan_payload = read_private_canonical_file(materialization.plan_path)
    try:
        request = FutureSessionPlanRequest.model_validate_json(request_payload)
        plan = _PLAN_ADAPTER.validate_json(plan_payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionMaterializationError("invalid_authority") from None
    if (
        canonical_request_json(request).encode() != request_payload
        or canonical_plan_json(plan).encode() != plan_payload
        or not isinstance(plan, ReadyToPrepareSessionPlan)
        or request.market is not FutureSessionMarket.KR
        or plan.market is not FutureSessionMarket.KR
        or plan.artifact_layout.root != output
    ):
        raise FutureSessionMaterializationError("invalid_authority")
    request_hash = hashlib.sha256(request_payload).hexdigest()
    recompiled = compile_future_session_plan(request)
    if (
        plan.source_request_sha256 != request_hash
        or not isinstance(recompiled, ReadyToPrepareSessionPlan)
        or canonical_plan_json(recompiled) != canonical_plan_json(plan)
    ):
        raise FutureSessionMaterializationError("authority_changed")
    interpreter = request.runtime_interpreter
    if interpreter is None or request.delivery_database is None:
        raise FutureSessionMaterializationError("kr_runtime_authority_missing")
    bundle_hash = plan.kr_rollover_bundle_sha256
    policy_hash = plan.kr_policy_sha256
    if bundle_hash is None or policy_hash is None:
        raise FutureSessionMaterializationError("kr_hash_authority_missing")
    entrypoint_root = (
        plan.frozen_runtime.directory
        if request.scheduler_authority_mode == "frozen_runtime"
        else request.authority_repository
    )
    entrypoint = entrypoint_root / "run_future_session_materialize.py"
    if not entrypoint.is_file() or not interpreter.is_file():
        raise FutureSessionMaterializationError("kr_supervisor_command_missing")
    if os.path.lexists(output):
        raise FutureSessionMaterializationError("output_already_exists")
    incident_queue_root = request.artifact_root.parent / "pending-execution-incidents"
    _ensure_incident_queue_root(incident_queue_root)
    output.parent.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.prepare-", dir=output.parent))
    stage.chmod(_DIRECTORY_MODE)
    try:
        manifest_path = output / "preparation-manifest.json"
        epochs = tuple(int(job.run_at.timestamp()) for job in plan.jobs)
        if len(epochs) != 6:
            raise FutureSessionMaterializationError("invalid_kr_phase_count")
        ledger_identity = experiment_ledger_v7_identity(request.experiment_ledger)
        entry = _prepare_supervisor(
            request=request,
            plan=plan,
            output=output,
            stage=stage,
            manifest_path=manifest_path,
            launch_agents=launch_agents,
            entrypoint=entrypoint,
            epochs=epochs,
            ledger_identity=ledger_identity,
            bundle_hash=bundle_hash,
            policy_hash=policy_hash,
            incident_queue_root=incident_queue_root,
        )
        manifest = KrFutureSessionPreparationManifest(
            target_session=plan.target_session.isoformat(),
            request_sha256=request_hash,
            plan_sha256=plan.plan_sha256,
            canonical_plan_file_sha256=hashlib.sha256(plan_payload).hexdigest(),
            request_file=materialization.request_path,
            plan_file=materialization.plan_path,
            scheduler_main_sha=plan.scheduler_main_sha,
            scheduler_authority_mode=request.scheduler_authority_mode,
            runtime_commit_sha=plan.frozen_runtime.commit_sha,
            authority_repository=request.authority_repository,
            frozen_runtime=plan.frozen_runtime.directory,
            runtime_interpreter=interpreter,
            experiment_ledger=request.experiment_ledger,
            experiment_ledger_identity_sha256=ledger_identity,
            kr_rollover_bundle_sha256=bundle_hash,
            kr_policy_sha256=policy_hash,
            internal_phase_epochs=epochs,
            entry=entry,
        )
        write_private_file(
            stage / "preparation-manifest.json",
            canonical_kr_manifest_json(manifest).encode(),
            _FILE_MODE,
        )
        os.replace(stage, output)
    except (OSError, ValueError):
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest_path


def _prepare_supervisor(
    *,
    request: FutureSessionPlanRequest,
    plan: ReadyToPrepareSessionPlan,
    output: Path,
    stage: Path,
    manifest_path: Path,
    launch_agents: Path,
    entrypoint: Path,
    epochs: tuple[int, ...],
    ledger_identity: str,
    bundle_hash: str,
    policy_hash: str,
    incident_queue_root: Path,
) -> PreparedKrSupervisorArtifact:
    if len(epochs) != 6 or request.runtime_interpreter is None:
        raise FutureSessionMaterializationError("invalid_kr_phase_count")
    phase_epochs = (epochs[0], epochs[1], epochs[2], epochs[3], epochs[4], epochs[5])
    jobs = stage / "jobs"
    receipts = stage / "receipts"
    logs = stage / "logs"
    incidents = stage / "execution-incidents"
    for directory in (jobs, receipts, incidents, logs):
        directory.mkdir(mode=_DIRECTORY_MODE)
    label = f"ai.trading-agent.future-session.kr.{plan.target_session.isoformat()}.supervisor"
    payload = output / "jobs" / "kr-supervisor.payload.zsh"
    wrapper = output / "jobs" / "kr-supervisor.persistent.zsh"
    plist = output / "jobs" / "kr-supervisor.plist"
    receipt = output / "receipts" / "kr-supervisor.json"
    stdout_log = output / "logs" / "kr-supervisor.stdout.log"
    stderr_log = output / "logs" / "kr-supervisor.stderr.log"
    payload_content = render_kr_supervisor_payload(
        KrSupervisorPayloadSpec(
            interpreter=request.runtime_interpreter,
            current_main_entrypoint=entrypoint,
            manifest=manifest_path,
            phase_epochs=phase_epochs,
            request_sha256=plan.source_request_sha256,
            plan_sha256=plan.plan_sha256,
            ledger_identity_sha256=ledger_identity,
            rollover_bundle_sha256=bundle_hash,
            policy_sha256=policy_hash,
        )
    ).encode()
    write_private_file(stage_path(stage, output, payload), payload_content, _EXECUTABLE_MODE)
    installed_plist = launch_agents / f"{label}.plist"
    wrapper_content = render_kr_restartable_runner(
        KrRestartableRunnerSpec(
            label=label,
            run_epoch=int(plan.jobs[0].run_at.timestamp()),
            expires_epoch=int(plan.jobs[-1].run_at.timestamp()) + 900,
            receipt=receipt,
            command=(str(payload),),
            persistent_plist=installed_plist,
            target_session=plan.target_session,
            incident_receipt=output / "execution-incidents" / "kr_supervisor.json",
            incident_queue_receipt=(incident_queue_root / f"kr--{plan.target_session.isoformat()}--kr_supervisor.json"),
            incident_fsync_interpreter=request.runtime_interpreter,
            manifest=manifest_path,
            request_sha256=plan.source_request_sha256,
            plan_sha256=plan.plan_sha256,
            scheduler_main_sha=plan.scheduler_main_sha,
            runtime_commit_sha=plan.frozen_runtime.commit_sha,
        )
    ).encode()
    write_private_file(stage_path(stage, output, wrapper), wrapper_content, _EXECUTABLE_MODE)
    plist_content = plistlib.dumps(
        {
            "Label": label,
            "ProcessType": "Background",
            "ProgramArguments": ["/bin/zsh", str(wrapper)],
            "KeepAlive": {"SuccessfulExit": False},
            "RunAtLoad": True,
            "StandardErrorPath": str(stderr_log),
            "StandardOutPath": str(stdout_log),
            "ThrottleInterval": 30,
            "Umask": 0o077,
        },
        sort_keys=True,
    )
    write_private_file(stage_path(stage, output, plist), plist_content, _FILE_MODE)
    return PreparedKrSupervisorArtifact(
        label=label,
        payload_wrapper=payload,
        payload_sha256=sha256(payload_content).hexdigest(),
        persistent_wrapper=wrapper,
        persistent_wrapper_sha256=sha256(wrapper_content).hexdigest(),
        persistent_plist=plist,
        persistent_plist_sha256=sha256(plist_content).hexdigest(),
        receipt=receipt,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )


def _ensure_incident_queue_root(path: Path) -> None:
    with suppress(FileExistsError):
        path.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=False)
    verify_private_directory(path)


__all__ = ("FutureSessionMaterializationError", "materialize_kr_future_session")
