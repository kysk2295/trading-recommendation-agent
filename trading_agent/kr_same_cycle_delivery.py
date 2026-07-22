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
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    InvalidHermesProjectionSourceError,
    project_opportunity_snapshots,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot

_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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
    "project_kr_same_cycle_delivery",
)
