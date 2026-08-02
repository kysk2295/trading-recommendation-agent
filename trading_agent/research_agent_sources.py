from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_source_adapters_primary import (
    DaySourceAdapter,
    MarketContextSourceAdapter,
    OpportunitySourceAdapter,
)
from trading_agent.research_agent_source_adapters_research import (
    DerivativesSourceAdapter,
    SwingSourceAdapter,
    SystematicSourceAdapter,
)
from trading_agent.research_agent_source_common import (
    InvalidResearchAgentSourceError,
    require_source_boundary,
)


class ResearchAgentSourcePaths(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outputs_root: Path
    market_context_root: Path
    day_session_root: Path
    swing_shadow_database: Path
    swing_review_database: Path
    experiment_ledger: Path
    lane_review_database: Path

    @model_validator(mode="after")
    def require_safe_boundaries(self) -> Self:
        for path in (
            self.outputs_root,
            self.market_context_root,
            self.day_session_root,
            self.swing_shadow_database,
            self.swing_review_database,
            self.experiment_ledger,
            self.lane_review_database,
        ):
            require_source_boundary(path)
        return self


class ResearchAgentSourceAdapter(Protocol):
    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]: ...


ADAPTERS: Final[tuple[ResearchAgentSourceAdapter, ...]] = (
    OpportunitySourceAdapter(),
    MarketContextSourceAdapter(),
    DaySourceAdapter(),
    SwingSourceAdapter(),
    SystematicSourceAdapter(),
    DerivativesSourceAdapter(),
)


def collect_research_agent_evidence(
    paths: ResearchAgentSourcePaths,
    *,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidResearchAgentSourceError(reason="collection_time_invalid")
    collected = tuple(evidence for adapter in ADAPTERS for evidence in adapter.collect(paths, now))
    family_order = {family: index for index, family in enumerate(PRIMARY_AGENT_FAMILIES)}
    return tuple(
        sorted(
            collected,
            key=lambda item: (family_order[item.agent_family_id], item.available_at, item.source_key),
        )
    )


__all__ = (
    "ADAPTERS",
    "InvalidResearchAgentSourceError",
    "ResearchAgentSourceAdapter",
    "ResearchAgentSourcePaths",
    "collect_research_agent_evidence",
)
