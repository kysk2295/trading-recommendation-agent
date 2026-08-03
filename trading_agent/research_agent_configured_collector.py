from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.research_agent_source_supply import (
    InvalidMarketContextSupplyError,
    MarketContextSupplyUnavailableError,
    materialize_current_market_context,
)
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
    ResearchAgentSourcePaths,
    collect_research_agent_evidence_isolated,
)


@dataclass(frozen=True, slots=True)
class ConfiguredResearchAgentEvidenceCollector:
    paths: ResearchAgentSourcePaths

    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch:
        supply_failure: InvalidMarketContextSupplyError | None = None
        try:
            _ = materialize_current_market_context(self.paths, now)
        except MarketContextSupplyUnavailableError as unavailable:
            _ = unavailable.reason
        except InvalidMarketContextSupplyError as error:
            supply_failure = error
        batch = collect_research_agent_evidence_isolated(self.paths, now=now)
        if supply_failure is None:
            return batch
        evidence = tuple(item for item in batch.evidence if item.agent_family_id != "market_context")
        failures = tuple(item for item in batch.failures if item.agent_family_id != "market_context")
        failure = ResearchAgentSourceFailure(
            agent_family_id="market_context",
            reason=f"market_context_supply.{supply_failure.reason}",
            observed_at=now,
        )
        return ResearchAgentSourceCollectionBatch(evidence, (*failures, failure))


__all__ = ("ConfiguredResearchAgentEvidenceCollector",)
