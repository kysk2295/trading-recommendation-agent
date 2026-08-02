from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict

from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_source_adapters_primary import (
    DaySourceAdapter,
    MarketContextSourceAdapter,
    OpportunitySourceAdapter,
)
from trading_agent.research_agent_source_common import InvalidResearchAgentSourceError
from trading_agent.research_agent_sources import ResearchAgentSourcePaths

PrimaryAgentFamily = Literal["opportunity_manager", "market_context", "day_trading"]
InspectionStatus = Literal["ready", "blocked"]


class PrimaryFamilySourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_family_id: PrimaryAgentFamily
    status: InspectionStatus
    source_key: str
    observed_at: dt.datetime
    provenance_sha256: tuple[str, ...]


class PrimarySourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: InspectionStatus
    inspected_at: dt.datetime
    families: tuple[
        PrimaryFamilySourceInspection,
        PrimaryFamilySourceInspection,
        PrimaryFamilySourceInspection,
    ]
    provider_calls: Literal[0] = 0
    broker_mutation: Literal[0] = 0


def inspect_primary_sources(
    paths: ResearchAgentSourcePaths,
    now: dt.datetime,
) -> PrimarySourceInspection:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidResearchAgentSourceError(reason="collection_time_invalid")
    collected = (
        OpportunitySourceAdapter().collect(paths, now),
        MarketContextSourceAdapter().collect(paths, now),
        DaySourceAdapter().collect(paths, now),
    )
    if any(len(items) != 1 for items in collected):
        raise InvalidResearchAgentSourceError(reason="primary_source_cardinality_invalid")
    opportunity = _family("opportunity_manager", collected[0][0])
    context = _family("market_context", collected[1][0])
    day = _family("day_trading", collected[2][0])
    families = (opportunity, context, day)
    status: InspectionStatus = "ready" if all(family.status == "ready" for family in families) else "blocked"
    return PrimarySourceInspection(status=status, inspected_at=now, families=families)


def _family(
    family: PrimaryAgentFamily,
    evidence: ResearchAgentEvidenceV1,
) -> PrimaryFamilySourceInspection:
    if evidence.agent_family_id != family:
        raise InvalidResearchAgentSourceError(reason="primary_source_family_invalid")
    status: InspectionStatus = "blocked" if ".blocked." in evidence.source_key else "ready"
    return PrimaryFamilySourceInspection(
        agent_family_id=family,
        status=status,
        source_key=evidence.source_key,
        observed_at=evidence.observed_at,
        provenance_sha256=evidence.evidence_refs,
    )


__all__ = (
    "PrimaryFamilySourceInspection",
    "PrimarySourceInspection",
    "inspect_primary_sources",
)
