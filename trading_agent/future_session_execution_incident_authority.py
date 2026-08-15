from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_coordinator_inspectors import CoordinatorInspectionError, inspect_request
from trading_agent.future_session_coordinator_service_models import FutureSessionCoordinatorServiceConfig
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    InvalidFutureSessionExecutionIncidentError,
)
from trading_agent.future_session_kr_manifest import (
    KrFutureSessionPreparationManifest,
    canonical_kr_manifest_json,
)
from trading_agent.future_session_materialization_models import (
    FutureSessionPreparationManifest,
    canonical_manifest_json,
)
from trading_agent.future_session_plan_models import FutureSessionMarket, FutureSessionUsRole, canonical_request_json
from trading_agent.future_session_us_activation_verifier import read_private_file


def validate_execution_incident_authority(
    config: FutureSessionCoordinatorServiceConfig,
    path: Path,
    receipt: FutureSessionExecutionIncidentReceipt,
) -> Path:
    root = path.parent.parent
    expected_runtime = config.state_root / "frozen-runtimes" / config.scheduler_main_sha
    if (
        path != root / "execution-incidents" / f"{receipt.role}.json"
        or root != config.state_root / "artifacts" / receipt.market.value / receipt.target_session.isoformat()
        or receipt.scheduler_main_sha != config.scheduler_main_sha
        or receipt.runtime_commit_sha != config.scheduler_main_sha
    ):
        raise InvalidFutureSessionExecutionIncidentError
    manifest_payload = read_private_file(root / "preparation-manifest.json", 0o600)
    if hashlib.sha256(manifest_payload).hexdigest() != receipt.manifest_sha256 or not _valid_manifest(
        config, expected_runtime, receipt, manifest_payload
    ):
        raise InvalidFutureSessionExecutionIncidentError
    request_path = config.state_root / "requests" / receipt.market.value / f"{receipt.target_session.isoformat()}.json"
    try:
        request = inspect_request(request_path)
    except CoordinatorInspectionError:
        raise InvalidFutureSessionExecutionIncidentError from None
    if (
        hashlib.sha256(canonical_request_json(request).encode()).hexdigest() != receipt.request_sha256
        or request.market is not receipt.market
        or request.scheduler_authority_mode != "frozen_runtime"
        or request.scheduler_main_sha != config.scheduler_main_sha
        or request.authority_repository != config.authority_repository
        or request.artifact_root != config.state_root / "artifacts"
        or request.frozen_runtime.directory != expected_runtime
        or request.frozen_runtime.commit_sha != config.scheduler_main_sha
        or request.delivery_database is None
    ):
        raise InvalidFutureSessionExecutionIncidentError
    return request.delivery_database


def _valid_manifest(
    config: FutureSessionCoordinatorServiceConfig,
    expected_runtime: Path,
    receipt: FutureSessionExecutionIncidentReceipt,
    payload: bytes,
) -> bool:
    try:
        if receipt.market is FutureSessionMarket.US:
            manifest = FutureSessionPreparationManifest.model_validate_json(payload)
            return (
                canonical_manifest_json(manifest).encode() == payload
                and manifest.request_sha256 == receipt.request_sha256
                and manifest.plan_sha256 == receipt.plan_sha256
                and manifest.scheduler_main_sha == config.scheduler_main_sha
                and manifest.runtime_commit_sha == config.scheduler_main_sha
                and manifest.authority_repository == config.authority_repository
                and manifest.frozen_runtime == expected_runtime
                and receipt.role in {role.value for role in FutureSessionUsRole}
            )
        manifest_kr = KrFutureSessionPreparationManifest.model_validate_json(payload)
        return (
            canonical_kr_manifest_json(manifest_kr).encode() == payload
            and manifest_kr.target_session == receipt.target_session.isoformat()
            and manifest_kr.request_sha256 == receipt.request_sha256
            and manifest_kr.plan_sha256 == receipt.plan_sha256
            and manifest_kr.scheduler_main_sha == config.scheduler_main_sha
            and manifest_kr.runtime_commit_sha == config.scheduler_main_sha
            and manifest_kr.authority_repository == config.authority_repository
            and manifest_kr.frozen_runtime == expected_runtime
            and receipt.role == "kr_supervisor"
        )
    except (TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None


__all__ = ("validate_execution_incident_authority",)
