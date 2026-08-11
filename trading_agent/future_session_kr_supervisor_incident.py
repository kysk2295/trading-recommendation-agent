from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from trading_agent.future_session_kr_supervisor_models import KrSupervisorPhase
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_same_cycle_delivery import (
    KrSourcePreflightDeliveryRequest,
    project_kr_source_preflight_incident,
)

_KST = dt.timezone(dt.timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class KrSupervisorIncidentRequest:
    manifest_sha256: str
    target_session: dt.date
    phase: KrSupervisorPhase
    delivery_database: Path
    strategy_version: str
    observed_at: dt.datetime


def project_kr_supervisor_incident(request: KrSupervisorIncidentRequest) -> None:
    projected_at = request.observed_at
    if projected_at.astimezone(_KST).date() != request.target_session:
        projected_at = dt.datetime.combine(request.target_session, dt.time(15, 45), tzinfo=_KST)
    _ = project_kr_source_preflight_incident(
        HermesDeliveryStore(request.delivery_database),
        KrSourcePreflightDeliveryRequest(
            collection_cycle_id=(
                f"kr-sup-{request.target_session:%Y%m%d}-{request.manifest_sha256[:16]}-{request.phase.value}"
            ),
            collection_date=request.target_session,
            strategy_version=request.strategy_version,
            projected_at=projected_at,
        ),
    )


__all__ = ("KrSupervisorIncidentRequest", "project_kr_supervisor_incident")
