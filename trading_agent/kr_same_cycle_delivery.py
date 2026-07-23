from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import (
    HERMES_DELIVERY_CONTRACT_VERSION,
    HermesDeliveryKind,
    hermes_delivery_id,
)
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    InvalidHermesProjectionSourceError,
    delivery_event_from_projection_record,
    project_opportunity_snapshots,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_source_cycle_delivery import (
    InvalidKrSourceCycleDeliveryError,
    KrSourceCycleDeliveryRequest,
    project_kr_source_cycle_incident,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot

_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KST: Final = dt.timezone(dt.timedelta(hours=9))


class InvalidKrSameCycleDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR same-cycle delivery projection is invalid"


@dataclass(frozen=True, slots=True)
class KrSameCycleDeliveryRequest:
    collection_cycle_id: str
    strategy_version: str
    occurred_at: dt.datetime
    opportunities: tuple[OpportunitySnapshot, ...]


@dataclass(frozen=True, slots=True)
class KrSourcePreflightDeliveryRequest:
    collection_cycle_id: str
    collection_date: dt.date
    strategy_version: str
    projected_at: dt.datetime


def project_kr_same_cycle_delivery(
    store: HermesDeliveryStore,
    request: KrSameCycleDeliveryRequest,
) -> HermesProjectionResult:
    _validate_request(request)
    try:
        with store.writer() as writer:
            if request.opportunities:
                return project_opportunity_snapshots(request.opportunities, writer)
            material = json.dumps(
                (request.collection_cycle_id, request.strategy_version, "censored_no_opportunity"),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(material.encode()).hexdigest()
            return project_outcomes(
                (
                    HermesProjectionRecord(
                        source_event_id=f"kr-same-cycle-no-opportunity-{digest}",
                        root_source_event_id=None,
                        kind=HermesDeliveryKind.NO_RECOMMENDATION,
                        market_id=MarketId.KR_EQUITIES.value,
                        agent_family=AgentFamily.OPPORTUNITY_MANAGER.value,
                        lane_id=None,
                        strategy_version=request.strategy_version,
                        instrument_id=None,
                        occurred_at=request.occurred_at,
                        status="censored_no_opportunity",
                        evidence_refs=(f"kr-collection-cycle:{request.collection_cycle_id}",),
                        rendered_text=(
                            "KR Opportunity Manager: completed source cycle produced no eligible opportunity."
                        ),
                        payload_sha256=digest,
                    ),
                ),
                writer,
            )
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesProjectionSourceError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrSameCycleDeliveryError from None


def project_kr_source_incident_if_available(
    source_store: KrThemeStore,
    delivery_store: HermesDeliveryStore,
    request: KrSourceCycleDeliveryRequest,
) -> bool:
    try:
        _ = project_kr_source_cycle_incident(source_store, delivery_store, request)
        return True
    except InvalidKrSourceCycleDeliveryError:
        return False


def project_kr_source_preflight_incident(
    store: HermesDeliveryStore,
    request: KrSourcePreflightDeliveryRequest,
) -> HermesProjectionResult:
    if (
        _IDENTIFIER.fullmatch(request.collection_cycle_id) is None
        or _IDENTIFIER.fullmatch(request.strategy_version) is None
        or request.projected_at.tzinfo is None
        or request.projected_at.utcoffset() is None
        or request.projected_at.astimezone(_KST).date() != request.collection_date
    ):
        raise InvalidKrSameCycleDeliveryError
    material = json.dumps(
        (
            request.collection_cycle_id,
            request.collection_date.isoformat(),
            request.strategy_version,
            "blocked_source_preflight",
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    source_event_id = f"kr-source-preflight-incident-{digest}"
    delivery_id = hermes_delivery_id(source_event_id, HERMES_DELIVERY_CONTRACT_VERSION)
    try:
        matches = tuple(event for event in store.events() if event.delivery_id == delivery_id)
        occurred_at = matches[0].occurred_at if len(matches) == 1 else request.projected_at
        record = HermesProjectionRecord(
            source_event_id=source_event_id,
            root_source_event_id=None,
            kind=HermesDeliveryKind.INCIDENT,
            market_id=MarketId.KR_EQUITIES.value,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER.value,
            lane_id=None,
            strategy_version=request.strategy_version,
            instrument_id=None,
            occurred_at=occurred_at,
            status="blocked_source_preflight",
            evidence_refs=(f"kr-collection-cycle:{request.collection_cycle_id}",),
            rendered_text=(
                "KR Opportunity Manager: recommendation blocked before source collection; "
                "required source preflight is incomplete."
            ),
            payload_sha256=digest,
        )
        if matches:
            if len(matches) != 1 or matches[0] != delivery_event_from_projection_record(record):
                raise InvalidKrSameCycleDeliveryError
            return HermesProjectionResult(examined=1, inserted=0, replayed=1)
        with store.writer() as writer:
            return project_outcomes((record,), writer)
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesProjectionSourceError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrSameCycleDeliveryError from None


def _validate_request(request: KrSameCycleDeliveryRequest) -> None:
    opportunity_ids = tuple(item.opportunity_id for item in request.opportunities)
    if (
        _IDENTIFIER.fullmatch(request.collection_cycle_id) is None
        or _IDENTIFIER.fullmatch(request.strategy_version) is None
        or request.occurred_at.tzinfo is None
        or request.occurred_at.utcoffset() is None
        or len(opportunity_ids) != len(set(opportunity_ids))
        or any(
            opportunity.strategy_lane.market_id is not MarketId.KR_EQUITIES
            or opportunity.strategy_lane.agent_family is not AgentFamily.OPPORTUNITY_MANAGER
            or opportunity.producer_strategy_version != request.strategy_version
            or not opportunity.observed_at <= request.occurred_at < opportunity.valid_until
            or tuple(
                evidence.record_id
                for evidence in opportunity.evidence_refs
                if evidence.namespace == "kr/collection_cycle"
            )
            != (request.collection_cycle_id,)
            for opportunity in request.opportunities
        )
    ):
        raise InvalidKrSameCycleDeliveryError


__all__ = (
    "InvalidKrSameCycleDeliveryError",
    "KrSameCycleDeliveryRequest",
    "KrSourceCycleDeliveryRequest",
    "KrSourcePreflightDeliveryRequest",
    "project_kr_same_cycle_delivery",
    "project_kr_source_incident_if_available",
    "project_kr_source_preflight_incident",
)
