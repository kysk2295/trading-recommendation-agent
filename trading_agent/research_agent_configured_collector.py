from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from trading_agent.research_agent_source_adapters_research import (
    SystematicGeneratedReviewSourceAdapter,
)
from trading_agent.research_agent_source_supply import (
    InvalidMarketContextSupplyError,
    MarketContextSupplyUnavailableError,
    materialize_current_market_context,
)
from trading_agent.research_agent_sources import (
    InvalidResearchAgentSourceError,
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
    ResearchAgentSourcePaths,
    collect_research_agent_evidence_isolated,
)


@dataclass(frozen=True, slots=True)
class ConfiguredResearchAgentEvidenceCollector:
    paths: ResearchAgentSourcePaths
    systematic_review_root: Path | None = None

    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch:
        supply_failure: InvalidMarketContextSupplyError | None = None
        try:
            _ = materialize_current_market_context(self.paths, now)
        except MarketContextSupplyUnavailableError as unavailable:
            _ = unavailable.reason
        except InvalidMarketContextSupplyError as error:
            supply_failure = error
        batch = collect_research_agent_evidence_isolated(self.paths, now=now)
        review_failure: ResearchAgentSourceFailure | None = None
        reviews = ()
        if self.systematic_review_root is not None:
            try:
                reviews = SystematicGeneratedReviewSourceAdapter().collect(self.systematic_review_root)
            except InvalidResearchAgentSourceError as error:
                review_failure = ResearchAgentSourceFailure(
                    agent_family_id="systematic_quant",
                    reason=error.reason,
                    observed_at=now,
                )
        if supply_failure is None and review_failure is None:
            return ResearchAgentSourceCollectionBatch((*batch.evidence, *reviews), batch.failures)
        evidence = batch.evidence
        failures = batch.failures
        if supply_failure is not None:
            evidence = tuple(item for item in evidence if item.agent_family_id != "market_context")
            failures = tuple(item for item in failures if item.agent_family_id != "market_context")
            failures = (
                *failures,
                ResearchAgentSourceFailure(
                    agent_family_id="market_context",
                    reason=f"market_context_supply.{supply_failure.reason}",
                    observed_at=now,
                ),
            )
        if review_failure is not None:
            failures = (*failures, review_failure)
        return ResearchAgentSourceCollectionBatch((*evidence, *reviews), failures)


__all__ = ("ConfiguredResearchAgentEvidenceCollector",)
