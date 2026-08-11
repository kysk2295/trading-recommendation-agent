from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from trading_agent.future_session_coordinator_activation_inspector import (
    inspect_activation,
)
from trading_agent.future_session_coordinator_inspectors import (
    CoordinatorInspectionError,
    inspect_plan,
    inspect_preparation,
    inspect_request,
)
from trading_agent.future_session_coordinator_models import (
    FutureSessionActivationResult,
    FutureSessionCoordinatorBlockReason,
    FutureSessionCoordinatorReceipt,
    FutureSessionCoordinatorRequest,
    FutureSessionCoordinatorResult,
    FutureSessionPreparationResult,
)
from trading_agent.future_session_coordinator_runtime import (
    acquire_coordinator_claim,
    launchctl_label_is_loaded,
    release_coordinator_claim,
)
from trading_agent.future_session_kr_activation import activate_kr_future_session
from trading_agent.future_session_kr_materializer import materialize_kr_future_session
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_plan_json,
)
from trading_agent.future_session_us_activation import activate_us_future_session
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
    LaunchctlRunner,
)
from trading_agent.future_session_us_materializer import materialize_us_future_session
from trading_agent.future_session_us_materializer_errors import (
    FutureSessionMaterializationError,
)
from trading_agent.future_session_us_materializer_io import write_private_file
from trading_agent.future_session_us_materializer_models import (
    UsFutureSessionMaterializationRequest,
)

type LabelStatusReader = Callable[[str], bool]


def coordinate_future_session(
    request: FutureSessionCoordinatorRequest,
    *,
    launchctl_runner: LaunchctlRunner | None = None,
    label_status_reader: LabelStatusReader | None = None,
) -> FutureSessionCoordinatorReceipt:
    authority = inspect_request(request.request_path)
    decision = compile_future_session_plan(authority)
    match decision:
        case WaitingSessionAuthority():
            return FutureSessionCoordinatorReceipt(
                result=FutureSessionCoordinatorResult.WAITING_AUTHORITY,
                market=decision.market,
                target_session=decision.target_session,
                preparation=FutureSessionPreparationResult.NOT_PREPARED,
                activation=FutureSessionActivationResult.NOT_ACTIVATED,
                waiting_reasons=decision.reasons,
            )
        case ReadyToPrepareSessionPlan():
            return _coordinate_ready(
                request,
                authority.market,
                decision,
                launchctl_runner,
                label_status_reader,
            )


def _coordinate_ready(
    request: FutureSessionCoordinatorRequest,
    market: FutureSessionMarket,
    plan: ReadyToPrepareSessionPlan,
    launchctl_runner: LaunchctlRunner | None,
    label_status_reader: LabelStatusReader | None,
) -> FutureSessionCoordinatorReceipt:
    preparation = FutureSessionPreparationResult.NOT_PREPARED
    claim = plan.artifact_layout.root.parent / f".{plan.artifact_layout.root.name}.coordinator.lock"
    try:
        descriptor = acquire_coordinator_claim(claim)
        try:
            plan_exists = inspect_plan(request.plan_path, plan)
            prepared = inspect_preparation(
                inspect_request(request.request_path),
                plan,
                request.plan_path,
                request.launch_agents_dir,
            )
            if prepared is None:
                if not plan_exists:
                    write_private_file(
                        request.plan_path,
                        canonical_plan_json(plan).encode(),
                        0o600,
                    )
                _materialize(market, request, plan)
                preparation = FutureSessionPreparationResult.PREPARED
                prepared = inspect_preparation(
                    inspect_request(request.request_path),
                    plan,
                    request.plan_path,
                    request.launch_agents_dir,
                )
                if prepared is None:
                    raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.MATERIALIZATION_FAILED)
            else:
                preparation = FutureSessionPreparationResult.ALREADY_PREPARED
            reader = launchctl_label_is_loaded if label_status_reader is None else label_status_reader
            if inspect_activation(market, prepared, reader):
                activation = FutureSessionActivationResult.ALREADY_ACTIVATED
            else:
                _activate(market, prepared.manifest_path, request, launchctl_runner)
                activation = FutureSessionActivationResult.ACTIVATED
            return FutureSessionCoordinatorReceipt(
                result=FutureSessionCoordinatorResult.ACTIVATED,
                market=market,
                target_session=plan.target_session,
                preparation=preparation,
                activation=activation,
                plan_path=request.plan_path,
                manifest_path=prepared.manifest_path,
                activation_receipt=prepared.receipt_path,
            )
        finally:
            release_coordinator_claim(claim, descriptor)
    except CoordinatorInspectionError as error:
        return _blocked(market, plan, request, preparation, error.reason)
    except FileExistsError:
        return _blocked(
            market,
            plan,
            request,
            preparation,
            FutureSessionCoordinatorBlockReason.DESTINATION_CLAIMED,
        )
    except FutureSessionMaterializationError as error:
        reason = (
            FutureSessionCoordinatorBlockReason.DESTINATION_CLAIMED
            if error.reason == "output_already_exists"
            else FutureSessionCoordinatorBlockReason.MATERIALIZATION_FAILED
        )
        return _blocked(
            market,
            plan,
            request,
            preparation,
            reason,
        )
    except FutureSessionActivationError as error:
        reason = (
            FutureSessionCoordinatorBlockReason.DESTINATION_CLAIMED
            if error.reason in ("activation_already_claimed", "schedule_already_claimed")
            else FutureSessionCoordinatorBlockReason.ACTIVATION_FAILED
        )
        return _blocked(
            market,
            plan,
            request,
            preparation,
            reason,
        )
    except OSError:
        return _blocked(
            market,
            plan,
            request,
            preparation,
            FutureSessionCoordinatorBlockReason.ARTIFACT_IO_FAILED,
        )


def _materialize(
    market: FutureSessionMarket,
    request: FutureSessionCoordinatorRequest,
    plan: ReadyToPrepareSessionPlan,
) -> None:
    match market:
        case FutureSessionMarket.US:
            materialize_us_future_session(
                UsFutureSessionMaterializationRequest(
                    request_path=request.request_path,
                    plan_path=request.plan_path,
                    output_dir=plan.artifact_layout.root,
                    launch_agents_dir=request.launch_agents_dir,
                )
            )
        case FutureSessionMarket.KR:
            materialize_kr_future_session(
                KrFutureSessionMaterializationRequest(
                    request_path=request.request_path,
                    plan_path=request.plan_path,
                    output_dir=plan.artifact_layout.root,
                    launch_agents_dir=request.launch_agents_dir,
                )
            )


def _activate(
    market: FutureSessionMarket,
    manifest_path: Path,
    request: FutureSessionCoordinatorRequest,
    launchctl_runner: LaunchctlRunner | None,
) -> None:
    match market:
        case FutureSessionMarket.US:
            activate_us_future_session(
                manifest_path=manifest_path,
                launch_agents_dir=request.launch_agents_dir,
                launchctl_runner=launchctl_runner,
            )
        case FutureSessionMarket.KR:
            activate_kr_future_session(
                manifest_path=manifest_path,
                launch_agents_dir=request.launch_agents_dir,
                launchctl_runner=launchctl_runner,
            )


def _blocked(
    market: FutureSessionMarket,
    plan: ReadyToPrepareSessionPlan,
    request: FutureSessionCoordinatorRequest,
    preparation: FutureSessionPreparationResult,
    reason: FutureSessionCoordinatorBlockReason,
) -> FutureSessionCoordinatorReceipt:
    return FutureSessionCoordinatorReceipt(
        result=FutureSessionCoordinatorResult.BLOCKED,
        market=market,
        target_session=plan.target_session,
        preparation=preparation,
        activation=FutureSessionActivationResult.NOT_ACTIVATED,
        plan_path=request.plan_path if os.path.lexists(request.plan_path) else None,
        manifest_path=(
            plan.artifact_layout.root / "preparation-manifest.json"
            if os.path.lexists(plan.artifact_layout.root / "preparation-manifest.json")
            else None
        ),
        reason=reason,
    )


__all__ = ("coordinate_future_session",)
