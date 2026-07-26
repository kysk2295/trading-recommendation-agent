from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final

from trading_agent.dashboard_derivatives_current_quote import read_current_option_quotes
from trading_agent.dashboard_derivatives_futures import read_futures_section
from trading_agent.dashboard_derivatives_options import read_options_section
from trading_agent.dashboard_derivatives_volatility import read_volatility_section
from trading_agent.dashboard_models_v2 import FreshnessV2, SourceStateV2
from trading_agent.dashboard_projection_common import WorkspaceProjection

_STATE_PRECEDENCE: Final = {
    "corrupt": 7,
    "error": 6,
    "blocked": 5,
    "unavailable": 4,
    "stale": 3,
    "populated": 2,
    "empty": 1,
}
_ITEM_CAP: Final = 24


def project_derivatives(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    current_quotes = read_current_option_quotes(outputs, now)
    sections = (
        current_quotes,
        read_options_section(outputs, now),
        read_volatility_section(outputs, now),
        read_futures_section(outputs, now),
    )
    state_sections = tuple(
        section
        for section in sections
        if not (current_quotes.state == "populated" and section.blocker_code == "current_quote_not_licensed")
    )
    selected = max(state_sections, key=lambda section: _STATE_PRECEDENCE[section.state])
    blocker = selected.blocker_code
    items = tuple(item for section in sections for item in section.items)[:_ITEM_CAP]
    total = sum(len(section.items) for section in sections)
    observed_at = max(
        (section.observed_at for section in sections if section.observed_at is not None),
        default=None,
    )
    state = selected.state if total > 0 or selected.state not in {"populated", "empty"} else "empty"
    return WorkspaceProjection(
        SourceStateV2(
            state=state,
            observed_at=observed_at,
            freshness=FreshnessV2(
                policy_id="typed-derivatives-authority-v2",
                age_seconds=(None if observed_at is None else max(0, int((now - observed_at).total_seconds()))),
                as_of=now,
            ),
            blocker_code=blocker,
            summary="Typed options, futures roll, and CFTC evidence projected",
            total_count=total,
            projected_count=len(items),
            truncated=total > len(items),
            trace_id=next(node.node_id for section in sections for node in section.nodes),
            items=items,
        ),
        tuple(node for section in sections for node in section.nodes),
        tuple(edge for section in sections for edge in section.edges),
    )


__all__ = ("project_derivatives",)
