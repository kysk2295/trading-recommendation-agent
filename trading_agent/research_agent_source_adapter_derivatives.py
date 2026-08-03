from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Protocol, final

from trading_agent.dashboard_projection_derivatives import project_derivatives
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_derivatives_payload import stable_derivatives_payload
from trading_agent.research_agent_source_common import (
    InvalidResearchAgentSourceError,
    ResearchAgentEvidenceMaterial,
    interval_bucket,
)


class DerivativesSourcePaths(Protocol):
    outputs_root: Path


@final
class DerivativesSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: DerivativesSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        projection = project_derivatives(paths.outputs_root, now=now)
        if projection.workspace.state in {"corrupt", "error"}:
            raise InvalidResearchAgentSourceError(reason="derivatives_source_invalid")
        blocker = projection.workspace.blocker_code
        source_key = (
            "derivatives.snapshot"
            if blocker in {None, "indicative_research_only"}
            else f"derivatives.blocked.{blocker}"
        )
        observed_at = projection.workspace.observed_at or interval_bucket(now, 15)
        payload = json.dumps(
            {
                "interval_observed_at": observed_at.isoformat(),
                "projection": json.loads(stable_derivatives_payload(projection)),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (
            ResearchAgentEvidenceMaterial(
                family="derivatives_research",
                trigger=ResearchAgentTriggerKind.MARKET_EVENT,
                source_key=source_key,
                observed_at=observed_at,
                available_at=observed_at,
                market_id="us_equities",
                canonical_payload=payload,
            ).evidence(),
        )


__all__ = ("DerivativesSourceAdapter", "DerivativesSourcePaths")
