from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.private_immutable_file import read_private_text
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_source_adapters_primary import (
    DaySourceAdapter,
    MarketContextSourceAdapter,
    OpportunitySourceAdapter,
    PrimarySourcePaths,
)
from trading_agent.research_agent_source_common import (
    InvalidResearchAgentSourceError,
    require_source_boundary,
)

PrimaryAgentFamily = Literal["opportunity_manager", "market_context", "day_trading"]
InspectionStatus = Literal["ready", "blocked"]


class PrimaryInspectionSourcePaths(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    market_context_root: Path
    day_session_root: Path

    @model_validator(mode="after")
    def require_safe_boundaries(self) -> Self:
        require_source_boundary(self.market_context_root)
        require_source_boundary(self.day_session_root)
        return self


class _PrimaryInspectionServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    source_paths: PrimaryInspectionSourcePaths


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
    paths: PrimarySourcePaths,
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


def load_primary_source_paths(config_path: Path) -> PrimaryInspectionSourcePaths:
    payload = read_private_text(config_path.expanduser().absolute())
    return _PrimaryInspectionServiceConfig.model_validate_json(payload).source_paths


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
    "PrimaryInspectionSourcePaths",
    "PrimarySourceInspection",
    "inspect_primary_sources",
    "load_primary_source_paths",
)
