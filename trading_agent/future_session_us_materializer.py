from __future__ import annotations

import hashlib
import os
import plistlib
import shlex
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from trading_agent.future_session_materialization_models import (
    FutureSessionPreparationManifest,
    PreparedUsRoleArtifact,
    canonical_manifest_json,
)
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
from trading_agent.launchd_one_shot_runner import (
    OneShotRunnerSpec,
    render_persistent_runner,
)

_PRIVATE_FILE_MODE = 0o600
_PRIVATE_EXECUTABLE_MODE = 0o700
_PRIVATE_DIRECTORY_MODE = 0o700
_PLAN_ADAPTER = TypeAdapter(FutureSessionPlanDecision)


@dataclass(frozen=True, slots=True)
class FutureSessionMaterializationError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def materialize_us_future_session(
    *,
    request_path: Path,
    plan_path: Path,
    output_dir: Path,
) -> Path:
    if not output_dir.is_absolute():
        raise FutureSessionMaterializationError("absolute_output_required")
    request_payload = _read_private_canonical_file(request_path)
    plan_payload = _read_private_canonical_file(plan_path)
    try:
        request = FutureSessionPlanRequest.model_validate_json(request_payload)
        plan = _PLAN_ADAPTER.validate_json(plan_payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionMaterializationError("invalid_authority") from None
    if (
        canonical_request_json(request).encode() != request_payload
        or canonical_plan_json(plan).encode() != plan_payload
        or not isinstance(plan, ReadyToPrepareSessionPlan)
        or request.market is not FutureSessionMarket.US
        or plan.market is not FutureSessionMarket.US
        or plan.artifact_layout.root != output_dir
    ):
        raise FutureSessionMaterializationError("invalid_authority")
    request_sha256 = _sha256(request_payload)
    if plan.source_request_sha256 != request_sha256:
        raise FutureSessionMaterializationError("request_plan_mismatch")
    recompiled = compile_future_session_plan(request)
    if (
        not isinstance(recompiled, ReadyToPrepareSessionPlan)
        or canonical_plan_json(recompiled) != canonical_plan_json(plan)
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
                authority_repository=request.authority_repository,
                manifest_path=manifest_path,
            )
            for job in plan.jobs
        )
        manifest = FutureSessionPreparationManifest(
            request_sha256=request_sha256,
            plan_sha256=plan.plan_sha256,
            canonical_plan_file_sha256=_sha256(plan_payload),
            scheduler_main_sha=plan.scheduler_main_sha,
            runtime_commit_sha=plan.frozen_runtime.commit_sha,
            runtime_attestation_sha256=runtime_environment.attestation_sha256,
            authority_repository=request.authority_repository,
            frozen_runtime=plan.frozen_runtime.directory,
            entries=entries,
        )
        _write_file(
            stage / "preparation-manifest.json",
            canonical_manifest_json(manifest).encode(),
            _PRIVATE_FILE_MODE,
        )
        os.replace(stage, output_dir)
    except BaseException:
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
) -> PreparedUsRoleArtifact:
    if (
        job.role is None
        or job.label is None
        or job.expires_at is None
        or not job.command
    ):
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
    payload_content = (
        "#!/bin/zsh\n\n"
        "set -u\n"
        "umask 077\n\n"
        f"exec {shlex.join(job.command)}\n"
    ).encode()
    _write_file(
        _stage_path(stage, output_dir, payload),
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
            persistent_plist=plist,
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
    _write_file(
        _stage_path(stage, output_dir, wrapper),
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
    _write_file(
        _stage_path(stage, output_dir, plist),
        plist_content,
        _PRIVATE_FILE_MODE,
    )
    return PreparedUsRoleArtifact(
        role=role,
        label=job.label,
        payload_wrapper=payload,
        payload_sha256=_sha256(payload_content),
        persistent_wrapper=wrapper,
        persistent_wrapper_sha256=_sha256(wrapper_content),
        persistent_plist=plist,
        persistent_plist_sha256=_sha256(plist_content),
        receipt=receipt,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )


def _read_private_canonical_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise FutureSessionMaterializationError("absolute_input_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
            or metadata.st_nlink != 1
        ):
            raise FutureSessionMaterializationError("invalid_input_file")
        payload = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        ):
            raise FutureSessionMaterializationError("input_changed")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _stage_path(stage: Path, output_dir: Path, final: Path) -> Path:
    return stage / final.relative_to(output_dir)


def _write_file(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        written = os.write(descriptor, content)
        if written != len(content):
            raise OSError("short write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = (
    "FutureSessionMaterializationError",
    "materialize_us_future_session",
)
