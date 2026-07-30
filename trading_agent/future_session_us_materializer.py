from __future__ import annotations

import os
import plistlib
import shutil
import tempfile
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from trading_agent.future_session_materialization_models import (
    FutureSessionPreparationManifest,
    PreparedUsRoleArtifact,
    canonical_manifest_json,
)
from trading_agent.future_session_payload_renderer import render_job_payload
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    JobTimingSpec,
    ReadyToPrepareSessionPlan,
    canonical_plan_json,
    canonical_request_json,
)
from trading_agent.future_session_us_materializer_errors import (
    FutureSessionMaterializationError,
)
from trading_agent.future_session_us_materializer_io import (
    sha256,
    stage_path,
    write_private_file,
)
from trading_agent.future_session_us_materializer_models import (
    UsFutureSessionMaterializationRequest,
)
from trading_agent.future_session_us_materializer_reader import (
    read_private_canonical_file,
)
from trading_agent.launchd_one_shot_runner import (
    OneShotRunnerSpec,
    render_persistent_runner,
)

_PRIVATE_FILE_MODE = 0o600
_PRIVATE_EXECUTABLE_MODE = 0o700
_PRIVATE_DIRECTORY_MODE = 0o700
_PLAN_ADAPTER = TypeAdapter(FutureSessionPlanDecision)


def materialize_us_future_session(
    materialization: UsFutureSessionMaterializationRequest,
) -> Path:
    request_path = materialization.request_path
    plan_path = materialization.plan_path
    output_dir = materialization.output_dir
    if not output_dir.is_absolute():
        raise FutureSessionMaterializationError("absolute_output_required")
    resolved_launch_agents_dir = (
        (Path.home() / "Library" / "LaunchAgents")
        if materialization.launch_agents_dir is None
        else materialization.launch_agents_dir
    )
    if not resolved_launch_agents_dir.is_absolute():
        raise FutureSessionMaterializationError("absolute_launch_agents_required")
    request_payload = read_private_canonical_file(request_path)
    plan_payload = read_private_canonical_file(plan_path)
    try:
        future_session_request = FutureSessionPlanRequest.model_validate_json(request_payload)
        plan = _PLAN_ADAPTER.validate_json(plan_payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionMaterializationError("invalid_authority") from None
    if (
        canonical_request_json(future_session_request).encode() != request_payload
        or canonical_plan_json(plan).encode() != plan_payload
        or not isinstance(plan, ReadyToPrepareSessionPlan)
        or future_session_request.market is not FutureSessionMarket.US
        or plan.market is not FutureSessionMarket.US
        or plan.artifact_layout.root != output_dir
    ):
        raise FutureSessionMaterializationError("invalid_authority")
    request_sha256 = sha256(request_payload).hexdigest()
    if plan.source_request_sha256 != request_sha256:
        raise FutureSessionMaterializationError("request_plan_mismatch")
    recompiled = compile_future_session_plan(future_session_request)
    if not isinstance(recompiled, ReadyToPrepareSessionPlan) or canonical_plan_json(recompiled) != canonical_plan_json(
        plan
    ):
        raise FutureSessionMaterializationError("authority_changed")
    runtime_environment = plan.runtime_environment
    if runtime_environment is None:
        raise FutureSessionMaterializationError("runtime_environment_invalid")
    if os.path.lexists(output_dir):
        raise FutureSessionMaterializationError("output_already_exists")
    output_dir.parent.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.prepare-",
            dir=output_dir.parent,
        )
    )
    stage.chmod(_PRIVATE_DIRECTORY_MODE)
    try:
        manifest_path = output_dir / "preparation-manifest.json"
        entries = tuple(
            _prepare_role(
                job=job,
                stage=stage,
                output_dir=output_dir,
                plan=plan,
                authority_repository=future_session_request.authority_repository,
                manifest_path=manifest_path,
                launch_agents_dir=resolved_launch_agents_dir,
            )
            for job in plan.jobs
        )
        manifest = FutureSessionPreparationManifest(
            request_sha256=request_sha256,
            plan_sha256=plan.plan_sha256,
            canonical_plan_file_sha256=sha256(plan_payload).hexdigest(),
            scheduler_main_sha=plan.scheduler_main_sha,
            runtime_commit_sha=plan.frozen_runtime.commit_sha,
            runtime_attestation_sha256=runtime_environment.attestation_sha256,
            authority_repository=future_session_request.authority_repository,
            frozen_runtime=plan.frozen_runtime.directory,
            entries=entries,
        )
        write_private_file(
            stage / "preparation-manifest.json",
            canonical_manifest_json(manifest).encode(),
            _PRIVATE_FILE_MODE,
        )
        os.replace(stage, output_dir)
    except (OSError, ValueError):
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest_path


def _prepare_role(
    *,
    job: JobTimingSpec,
    stage: Path,
    output_dir: Path,
    plan: ReadyToPrepareSessionPlan,
    authority_repository: Path,
    manifest_path: Path,
    launch_agents_dir: Path,
) -> PreparedUsRoleArtifact:
    if job.role is None or job.label is None or job.expires_at is None or not job.command:
        raise FutureSessionMaterializationError("invalid_us_role")
    role = job.role
    jobs_stage = stage / "jobs"
    receipts_stage = stage / "receipts"
    logs_stage = stage / "logs"
    for directory in (jobs_stage, receipts_stage, logs_stage):
        directory.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
        directory.chmod(_PRIVATE_DIRECTORY_MODE)
    payload = output_dir / "jobs" / f"{role.value}.payload.zsh"
    wrapper = output_dir / "jobs" / f"{role.value}.persistent.zsh"
    plist = output_dir / "jobs" / f"{role.value}.plist"
    receipt = output_dir / "receipts" / f"{role.value}.json"
    stdout_log = output_dir / "logs" / f"{role.value}.stdout.log"
    stderr_log = output_dir / "logs" / f"{role.value}.stderr.log"
    payload_content = render_job_payload(job).encode()
    write_private_file(
        stage_path(stage, output_dir, payload),
        payload_content,
        _PRIVATE_EXECUTABLE_MODE,
    )
    runtime_environment = plan.runtime_environment
    if runtime_environment is None:
        raise FutureSessionMaterializationError("runtime_environment_invalid")
    wrapper_content = render_persistent_runner(
        OneShotRunnerSpec(
            label=job.label,
            run_at=job.run_at,
            receipt=receipt,
            command=(str(payload),),
            expires_at=job.expires_at,
            persistent_plist=launch_agents_dir / f"{job.label}.plist",
            authority_repository=authority_repository,
            source_commit=plan.scheduler_main_sha,
            role=role.value,
            request_sha256=plan.source_request_sha256,
            plan_sha256=plan.plan_sha256,
            runtime_commit_sha=plan.frozen_runtime.commit_sha,
            runtime_attestation_sha256=runtime_environment.attestation_sha256,
            preparation_manifest=manifest_path,
        )
    ).encode()
    write_private_file(
        stage_path(stage, output_dir, wrapper),
        wrapper_content,
        _PRIVATE_EXECUTABLE_MODE,
    )
    plist_content = plistlib.dumps(
        {
            "Label": job.label,
            "ProcessType": "Background",
            "ProgramArguments": ["/bin/zsh", str(wrapper)],
            "RunAtLoad": True,
            "StandardErrorPath": str(stderr_log),
            "StandardOutPath": str(stdout_log),
            "ThrottleInterval": 30,
            "Umask": 0o077,
        },
        sort_keys=True,
    )
    write_private_file(
        stage_path(stage, output_dir, plist),
        plist_content,
        _PRIVATE_FILE_MODE,
    )
    return PreparedUsRoleArtifact(
        role=role,
        label=job.label,
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


__all__ = (
    "FutureSessionMaterializationError",
    "materialize_us_future_session",
)
