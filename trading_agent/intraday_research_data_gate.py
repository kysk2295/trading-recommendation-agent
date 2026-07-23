from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.data_capability_models import DataUse
from trading_agent.data_foundation_manifest import load_data_foundation_artifact
from trading_agent.intraday_research_loop_models import IntradayResearchManifest
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.strategy_data_gate import StrategyDataStatus


class InvalidIntradayResearchDataError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday research data foundation is invalid"


def require_intraday_research_data(
    manifest: IntradayResearchManifest,
    foundation_paths: tuple[Path, ...],
) -> None:
    if manifest.schema_version == 1:
        if foundation_paths:
            raise InvalidIntradayResearchDataError
        return
    if len(foundation_paths) != len(manifest.hypotheses):
        raise InvalidIntradayResearchDataError
    artifacts = tuple(load_data_foundation_artifact(path) for path in foundation_paths)
    by_hash = {artifact.sha256: artifact.manifest for artifact in artifacts}
    if len(by_hash) != len(artifacts):
        raise InvalidIntradayResearchDataError
    for selection in manifest.hypotheses:
        foundation_hash = selection.data_foundation_sha256
        if foundation_hash is None or foundation_hash not in by_hash:
            raise InvalidIntradayResearchDataError
        foundation = by_hash[foundation_hash]
        lane = foundation.strategy_lane
        decision = foundation.evaluate_data_readiness()
        if (
            lane.market_id is not MarketId.US_EQUITIES
            or lane.agent_family is not AgentFamily.DAY_TRADING
            or lane.strategy_id != selection.strategy.value
            or foundation.evaluated_at > manifest.registered_at
            or decision.status is not StrategyDataStatus.READY
            or any(
                requirement.data_use is not DataUse.HISTORICAL_RESEARCH or requirement.event_type != "minute_bar"
                for requirement in foundation.requirements
            )
        ):
            raise InvalidIntradayResearchDataError


__all__ = (
    "InvalidIntradayResearchDataError",
    "require_intraday_research_data",
)
