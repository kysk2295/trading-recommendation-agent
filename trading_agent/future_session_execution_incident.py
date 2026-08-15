from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.future_session_plan_models import FutureSessionMarket
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


__all__ = (
    "FutureSessionExecutionIncidentReceipt",
    "InvalidFutureSessionExecutionIncidentError",
    "canonical_execution_incident_json",
    "project_execution_incident",
)
