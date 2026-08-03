from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_projection_derivatives import project_derivatives

DerivativesSupplyState = Literal["ready", "operator_action_required", "blocked"]


@dataclass(frozen=True, slots=True)
class DerivativesSupplyClassification:
    state: DerivativesSupplyState
    reason: str
    next_action: str


def classify_derivatives_supply(outputs: Path, now: dt.datetime) -> DerivativesSupplyClassification:
    projection = project_derivatives(outputs, now=now).workspace
    if projection.state in {"corrupt", "error"}:
        return DerivativesSupplyClassification(
            "blocked",
            "derivatives_source_invalid",
            "repair_derivatives_source_integrity",
        )
    indicative_research = any(
        item.value is not None and item.value.endswith(":research_only") for item in projection.items
    )
    if indicative_research:
        return DerivativesSupplyClassification(
            "ready",
            "indicative_research_ready_not_opra",
            "continue_indicative_research_only",
        )
    if projection.projected_count > 0 and projection.blocker_code == "current_quote_not_licensed":
        return DerivativesSupplyClassification(
            "ready",
            "research_shadow_available_realtime_entitlement_missing",
            "continue_research_shadow_only",
        )
    if projection.blocker_code == "options_entitlement_missing":
        return DerivativesSupplyClassification(
            "operator_action_required",
            "external_realtime_entitlement_unverified",
            "obtain_reviewed_derivatives_research_entitlement",
        )
    if projection.blocker_code is not None:
        return DerivativesSupplyClassification(
            "blocked",
            projection.blocker_code,
            "repair_derivatives_research_source",
        )
    return DerivativesSupplyClassification(
        "ready",
        "reviewed_research_source_ready",
        "continue_research_shadow_only",
    )


__all__ = (
    "DerivativesSupplyClassification",
    "DerivativesSupplyState",
    "classify_derivatives_supply",
)
