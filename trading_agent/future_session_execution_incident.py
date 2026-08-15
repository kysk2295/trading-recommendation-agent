from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.future_session_coordinator_inspectors import CoordinatorInspectionError, inspect_request
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
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
    FutureSessionUsRole,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_verifier import read_private_file
from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_identity_models import AgentFamily, MarketId

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MARKET_ZONES = {
    FutureSessionMarket.US: ZoneInfo("America/New_York"),
    FutureSessionMarket.KR: ZoneInfo("Asia/Seoul"),
}


class InvalidFutureSessionExecutionIncidentError(ValueError):
    pass


class FutureSessionExecutionIncidentReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    completed_at_epoch: int = Field(ge=0)
    market: FutureSessionMarket
    target_session: dt.date
    role: str
    reason: Literal["runtime_authority_invalid"]
    manifest_sha256: str
    request_sha256: str
    plan_sha256: str
    scheduler_main_sha: str
    runtime_commit_sha: str

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            not self.role
            or self.role != self.role.strip()
            or _SHA256.fullmatch(self.manifest_sha256) is None
            or _SHA256.fullmatch(self.request_sha256) is None
            or _SHA256.fullmatch(self.plan_sha256) is None
            or _GIT_SHA.fullmatch(self.scheduler_main_sha) is None
            or _GIT_SHA.fullmatch(self.runtime_commit_sha) is None
        ):
            raise InvalidFutureSessionExecutionIncidentError
        return self


def canonical_execution_incident_json(receipt: FutureSessionExecutionIncidentReceipt) -> str:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def project_pending_execution_incidents(
    config: FutureSessionCoordinatorServiceConfig,
) -> frozenset[tuple[FutureSessionMarket, dt.date]]:
    artifact_root = config.state_root / "artifacts"
    if not artifact_root.exists():
        return frozenset()
    projected: set[tuple[FutureSessionMarket, dt.date]] = set()
    for path in sorted(artifact_root.glob("*/*/execution-incidents/*.json")):
        receipt = _read_receipt(path)
        delivery_database = _validate_receipt_authority(config, path, receipt)
        project_execution_incident(receipt, delivery_database)
        projected.add((receipt.market, receipt.target_session))
    return frozenset(projected)


def project_execution_incident(
    receipt: FutureSessionExecutionIncidentReceipt,
    delivery_database: Path,
) -> None:
    receipt = FutureSessionExecutionIncidentReceipt.model_validate(receipt.model_dump(mode="python"))
    material = canonical_execution_incident_json(receipt)
    digest = hashlib.sha256(material.encode()).hexdigest()
    occurred_at = dt.datetime.fromtimestamp(receipt.completed_at_epoch, tz=dt.UTC)
    zone = _MARKET_ZONES[receipt.market]
    if occurred_at.astimezone(zone).date() != receipt.target_session:
        occurred_at = dt.datetime.combine(receipt.target_session, dt.time(12), tzinfo=zone)
    market_id = MarketId.US_EQUITIES if receipt.market is FutureSessionMarket.US else MarketId.KR_EQUITIES
    agent_family = (
        AgentFamily.DAY_TRADING if receipt.market is FutureSessionMarket.US else AgentFamily.OPPORTUNITY_MANAGER
    )
    prefix = "us-future-session-incident" if receipt.market is FutureSessionMarket.US else "kr-future-session-incident"
    record = HermesProjectionRecord(
        source_event_id=f"{prefix}-{digest}",
        root_source_event_id=None,
        kind=HermesDeliveryKind.INCIDENT,
        market_id=market_id.value,
        agent_family=agent_family.value,
        lane_id=None,
        strategy_version=None,
        instrument_id=None,
        occurred_at=occurred_at,
        status=receipt.reason,
        evidence_refs=tuple(
            sorted(
                (
                    f"manifest-sha256:{receipt.manifest_sha256}",
                    f"plan-sha256:{receipt.plan_sha256}",
                    f"request-sha256:{receipt.request_sha256}",
                    f"role:{receipt.role}",
                    f"runtime-commit:{receipt.runtime_commit_sha}",
                    f"scheduler-main:{receipt.scheduler_main_sha}",
                )
            )
        ),
        rendered_text=(
            f"{receipt.market.value.upper()} session {receipt.target_session.isoformat()} was blocked before "
            "execution because frozen runtime authority validation failed. No trading action was performed."
        ),
        payload_sha256=digest,
    )
    try:
        with HermesDeliveryStore(delivery_database).writer() as writer:
            _ = project_outcomes((record,), writer)
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        OSError,
        sqlite3.Error,
    ):
        raise InvalidFutureSessionExecutionIncidentError from None


def _read_receipt(path: Path) -> FutureSessionExecutionIncidentReceipt:
    try:
        payload = read_private_file(path, 0o600)
        receipt = FutureSessionExecutionIncidentReceipt.model_validate_json(payload)
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None
    if canonical_execution_incident_json(receipt).encode() != payload:
        raise InvalidFutureSessionExecutionIncidentError
    return receipt


def _validate_receipt_authority(
    config: FutureSessionCoordinatorServiceConfig,
    path: Path,
    receipt: FutureSessionExecutionIncidentReceipt,
) -> Path:
    root = path.parent.parent
    if (
        path.parent.name != "execution-incidents"
        or root.name != receipt.target_session.isoformat()
        or root.parent.name != receipt.market.value
        or path.name != f"{receipt.role}.json"
    ):
        raise InvalidFutureSessionExecutionIncidentError
    manifest_path = root / "preparation-manifest.json"
    payload = read_private_file(manifest_path, 0o600)
    if hashlib.sha256(payload).hexdigest() != receipt.manifest_sha256:
        raise InvalidFutureSessionExecutionIncidentError
    try:
        if receipt.market is FutureSessionMarket.US:
            manifest = FutureSessionPreparationManifest.model_validate_json(payload)
            valid_manifest = (
                canonical_manifest_json(manifest).encode() == payload
                and manifest.request_sha256 == receipt.request_sha256
                and manifest.plan_sha256 == receipt.plan_sha256
                and manifest.scheduler_main_sha == receipt.scheduler_main_sha
                and manifest.runtime_commit_sha == receipt.runtime_commit_sha
                and receipt.role in {role.value for role in FutureSessionUsRole}
            )
        else:
            manifest_kr = KrFutureSessionPreparationManifest.model_validate_json(payload)
            valid_manifest = (
                canonical_kr_manifest_json(manifest_kr).encode() == payload
                and manifest_kr.target_session == receipt.target_session.isoformat()
                and manifest_kr.request_sha256 == receipt.request_sha256
                and manifest_kr.plan_sha256 == receipt.plan_sha256
                and manifest_kr.scheduler_main_sha == receipt.scheduler_main_sha
                and manifest_kr.runtime_commit_sha == receipt.runtime_commit_sha
                and receipt.role == "kr_supervisor"
            )
    except (TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None
    if not valid_manifest:
        raise InvalidFutureSessionExecutionIncidentError
    request_path = config.state_root / "requests" / receipt.market.value / f"{receipt.target_session.isoformat()}.json"
    try:
        request = inspect_request(request_path)
    except CoordinatorInspectionError:
        raise InvalidFutureSessionExecutionIncidentError from None
    if (
        hashlib.sha256(canonical_request_json(request).encode()).hexdigest() != receipt.request_sha256
        or request.market is not receipt.market
        or request.scheduler_main_sha != receipt.scheduler_main_sha
        or request.frozen_runtime.commit_sha != receipt.runtime_commit_sha
        or request.delivery_database is None
    ):
        raise InvalidFutureSessionExecutionIncidentError
    return request.delivery_database


__all__ = (
    "FutureSessionExecutionIncidentReceipt",
    "InvalidFutureSessionExecutionIncidentError",
    "canonical_execution_incident_json",
    "project_execution_incident",
    "project_pending_execution_incidents",
)
