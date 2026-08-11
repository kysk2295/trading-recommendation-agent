from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorBlockReason,
)
from trading_agent.future_session_kr_activation_verifier import (
    verify_kr_future_session_activation,
)
from trading_agent.future_session_kr_manifest import (
    KrFutureSessionPreparationManifest,
    canonical_kr_manifest_json,
)
from trading_agent.future_session_materialization_models import (
    FutureSessionPreparationManifest,
    canonical_manifest_json,
)
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    canonical_plan_json,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)
from trading_agent.future_session_us_activation_verifier import (
    PRIVATE_FILE_MODE,
    read_private_file,
    verify_us_future_session_activation,
)
from trading_agent.future_session_us_materializer_errors import (
    FutureSessionMaterializationError,
)
from trading_agent.future_session_us_materializer_reader import (
    read_private_canonical_file,
)

_PLAN_ADAPTER = TypeAdapter(FutureSessionPlanDecision)


@dataclass(frozen=True, slots=True)
class CoordinatorInspectionError(Exception):
    reason: FutureSessionCoordinatorBlockReason

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PreparedSchedule:
    manifest_path: Path
    manifest_sha256: str
    labels: tuple[str, ...]
    source_plists: tuple[Path, ...]
    installed_plists: tuple[Path, ...]
    receipt_path: Path


def inspect_request(path: Path) -> FutureSessionPlanRequest:
    try:
        payload = read_private_canonical_file(path)
        request = FutureSessionPlanRequest.model_validate_json(payload)
    except (FutureSessionMaterializationError, TypeError, ValidationError, ValueError):
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.INVALID_REQUEST
        ) from None
    if canonical_request_json(request).encode() != payload:
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.INVALID_REQUEST
        )
    return request


def inspect_plan(path: Path, expected: ReadyToPrepareSessionPlan) -> bool:
    if not os.path.lexists(path):
        return False
    try:
        payload = read_private_canonical_file(path)
        plan = _PLAN_ADAPTER.validate_json(payload)
    except (FutureSessionMaterializationError, TypeError, ValidationError, ValueError):
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.PLAN_CONFLICT
        ) from None
    if (
        canonical_plan_json(plan).encode() != payload
        or canonical_plan_json(plan) != canonical_plan_json(expected)
    ):
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.PLAN_CONFLICT
        )
    return True


def inspect_preparation(
    request: FutureSessionPlanRequest,
    plan: ReadyToPrepareSessionPlan,
    plan_path: Path,
    launch_agents_dir: Path,
) -> PreparedSchedule | None:
    root = plan.artifact_layout.root
    if not os.path.lexists(root):
        return None
    manifest_path = root / "preparation-manifest.json"
    if not os.path.lexists(manifest_path):
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.PREPARATION_CONFLICT
        )
    try:
        match request.market:
            case FutureSessionMarket.US:
                verified = verify_us_future_session_activation(
                    manifest_path=manifest_path,
                    launch_agents_dir=launch_agents_dir,
                )
                payload = read_private_file(manifest_path, PRIVATE_FILE_MODE)
                manifest = FutureSessionPreparationManifest.model_validate_json(payload)
                if (
                    canonical_manifest_json(manifest).encode() != payload
                    or manifest.request_sha256 != plan.source_request_sha256
                    or manifest.plan_sha256 != plan.plan_sha256
                    or manifest.canonical_plan_file_sha256
                    != hashlib.sha256(canonical_plan_json(plan).encode()).hexdigest()
                ):
                    raise CoordinatorInspectionError(
                        FutureSessionCoordinatorBlockReason.PREPARATION_CONFLICT
                    )
                return PreparedSchedule(
                    manifest_path=manifest_path,
                    manifest_sha256=verified.manifest_sha256,
                    labels=tuple(entry.label for entry in verified.entries),
                    source_plists=tuple(entry.source_plist for entry in verified.entries),
                    installed_plists=tuple(
                        entry.installed_plist for entry in verified.entries
                    ),
                    receipt_path=verified.receipt_path,
                )
            case FutureSessionMarket.KR:
                verified_kr = verify_kr_future_session_activation(
                    manifest_path=manifest_path,
                    launch_agents_dir=launch_agents_dir,
                )
                payload = read_private_file(manifest_path, PRIVATE_FILE_MODE)
                manifest_kr = KrFutureSessionPreparationManifest.model_validate_json(
                    payload
                )
                if (
                    canonical_kr_manifest_json(manifest_kr).encode() != payload
                    or manifest_kr.request_sha256 != plan.source_request_sha256
                    or manifest_kr.plan_sha256 != plan.plan_sha256
                    or manifest_kr.plan_file != plan_path
                ):
                    raise CoordinatorInspectionError(
                        FutureSessionCoordinatorBlockReason.PREPARATION_CONFLICT
                    )
                return PreparedSchedule(
                    manifest_path=manifest_path,
                    manifest_sha256=verified_kr.manifest_sha256,
                    labels=(verified_kr.label,),
                    source_plists=(verified_kr.source_plist,),
                    installed_plists=(verified_kr.installed_plist,),
                    receipt_path=verified_kr.receipt_path,
                )
    except CoordinatorInspectionError:
        raise
    except (FutureSessionActivationError, OSError, TypeError, ValidationError, ValueError):
        raise CoordinatorInspectionError(
            FutureSessionCoordinatorBlockReason.PREPARATION_CONFLICT
        ) from None


__all__ = (
    "CoordinatorInspectionError",
    "PreparedSchedule",
    "inspect_plan",
    "inspect_preparation",
    "inspect_request",
)
