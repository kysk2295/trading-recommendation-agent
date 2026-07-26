from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.dashboard_derivatives_futures import read_futures_section
from trading_agent.dashboard_derivatives_options import read_options_section
from trading_agent.dashboard_models_v2 import FreshnessV2, SourceStateV2
from trading_agent.dashboard_projection_common import WorkspaceProjection


def project_derivatives(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    sections = (
        read_options_section(outputs, now),
        read_futures_section(outputs, now),
    )
    blocker = next(
        (section.blocker_code for section in sections if section.blocker_code is not None),
        None,
    )
    items = tuple(item for section in sections for item in section.items)[:50]
    total = sum(len(section.items) for section in sections)
    observed_at = max(
        (section.observed_at for section in sections if section.observed_at is not None),
        default=None,
    )
    state = (
        "unavailable"
        if all(section.state == "unavailable" for section in sections)
        else "corrupt"
        if any(section.state == "corrupt" for section in sections)
        else "blocked"
        if blocker is not None
        else "stale"
        if any(section.state == "stale" for section in sections)
        else "empty"
        if total == 0
        else "populated"
    )
    return WorkspaceProjection(
        SourceStateV2(
            state=state,
            observed_at=observed_at,
            freshness=FreshnessV2(
                policy_id="typed-derivatives-authority-v2",
                age_seconds=(
                    None
                    if observed_at is None
                    else max(0, int((now - observed_at).total_seconds()))
                ),
                as_of=now,
            ),
            blocker_code=blocker,
            summary="Typed options, futures roll, and CFTC evidence projected",
            total_count=total,
            projected_count=len(items),
            truncated=total > len(items),
            trace_id=sections[0].nodes[0].node_id,
            items=items,
        ),
        tuple(node for section in sections for node in section.nodes),
        tuple(edge for section in sections for edge in section.edges),
    )


__all__ = ("project_derivatives",)
