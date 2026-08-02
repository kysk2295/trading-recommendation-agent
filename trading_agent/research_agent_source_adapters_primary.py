from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, final

from pydantic import ValidationError

from trading_agent.hermes_delivery_projection import (
    InvalidHermesProjectionSourceError,
    read_opportunity_snapshots,
)
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_source_common import (
    CapabilityEvidenceSpec,
    InvalidResearchAgentSourceError,
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    canonical_payload_json,
    capability_evidence,
    require_private_source_file,
    require_source_boundary,
)

if TYPE_CHECKING:
    from trading_agent.research_agent_sources import ResearchAgentSourcePaths


@final
class OpportunitySourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        outboxes = tuple(
            session / "opportunities.v1.jsonl"
            for session in _latest_session_directories(paths.day_session_root)
            if (session / "opportunities.v1.jsonl").exists()
        )
        projected: list[ResearchAgentEvidenceV1] = []
        try:
            for outbox in outboxes:
                require_source_boundary(outbox)
                for snapshot in read_opportunity_snapshots(outbox):
                    projected.append(
                        ResearchAgentEvidenceMaterial(
                            family="opportunity_manager",
                            trigger=ResearchAgentTriggerKind.NEW_DATA,
                            source_key=f"opportunity.{snapshot.opportunity_id}",
                            observed_at=snapshot.observed_at,
                            available_at=snapshot.observed_at,
                            market_id=snapshot.strategy_lane.market_id.value,
                            canonical_payload=canonical_model_json(snapshot),
                        ).evidence()
                    )
        except (InvalidHermesProjectionSourceError, OSError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="opportunity_source_invalid") from None
        if projected:
            return tuple(projected)
        return (
            capability_evidence(
                CapabilityEvidenceSpec(
                    family="opportunity_manager",
                    source_key="opportunity.blocked.snapshot_unavailable",
                    market_id="none",
                ),
                now,
            ),
        )


@final
class MarketContextSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        if not paths.market_context_root.exists():
            return (
                capability_evidence(
                    CapabilityEvidenceSpec(
                        family="market_context",
                        source_key="market_context.blocked.snapshot_unavailable",
                        market_id="cross_market",
                    ),
                    now,
                ),
            )
        require_source_boundary(paths.market_context_root)
        artifacts = tuple(sorted(paths.market_context_root.glob("*.market-context.json"))[-8:])
        if not artifacts:
            return (
                capability_evidence(
                    CapabilityEvidenceSpec(
                        family="market_context",
                        source_key="market_context.blocked.snapshot_unavailable",
                        market_id="cross_market",
                    ),
                    now,
                ),
            )
        projected: list[ResearchAgentEvidenceV1] = []
        try:
            for artifact in artifacts:
                require_private_source_file(artifact)
                snapshot = MarketContextSnapshot.model_validate_json(artifact.read_text(encoding="utf-8"))
                projected.append(
                    ResearchAgentEvidenceMaterial(
                        family="market_context",
                        trigger=ResearchAgentTriggerKind.MARKET_EVENT,
                        source_key=f"market_context.{snapshot.context_id}",
                        observed_at=snapshot.observed_at,
                        available_at=snapshot.observed_at,
                        market_id=snapshot.market_id.value,
                        canonical_payload=canonical_model_json(snapshot),
                    ).evidence()
                )
        except (InvalidResearchAgentSourceError, OSError, UnicodeError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="market_context_source_invalid") from None
        return tuple(projected)


@final
class DaySourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        databases = tuple(
            session / "paper_recommendations.sqlite3"
            for session in _latest_session_directories(paths.day_session_root)
            if (session / "paper_recommendations.sqlite3").exists()
        )
        if not databases:
            return (
                capability_evidence(
                    CapabilityEvidenceSpec(
                        family="day_trading",
                        source_key="day.blocked.completed_bar_unavailable",
                        market_id="us_equities",
                    ),
                    now,
                ),
            )
        try:
            return tuple(_day_database_evidence(database) for database in databases)
        except (InvalidResearchAgentSourceError, OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidResearchAgentSourceError(reason="day_source_invalid") from None


def _latest_session_directories(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    require_source_boundary(root)
    sessions = tuple(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and len(path.name) == 8 and path.name.isdigit()
    )
    return tuple(sorted(sessions, key=lambda path: path.name)[-2:])


def _day_database_evidence(database: Path) -> ResearchAgentEvidenceV1:
    require_source_boundary(database)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        recommendations = connection.execute("SELECT COUNT(*),MAX(created_at) FROM recommendations").fetchone()
        checkpoints = connection.execute("SELECT COUNT(*),MAX(processed_at) FROM bar_checkpoints").fetchone()
        events = connection.execute("SELECT COUNT(*),MAX(occurred_at) FROM events").fetchone()
    if integrity != ("ok",) or recommendations is None or checkpoints is None or events is None:
        raise InvalidResearchAgentSourceError(reason="day_database_invalid")
    timestamps = tuple(
        dt.datetime.fromisoformat(value)
        for value in (recommendations[1], checkpoints[1], events[1])
        if value is not None
    )
    observed_at = max(timestamps, default=dt.datetime.fromtimestamp(database.stat().st_mtime, tz=dt.UTC))
    payload = canonical_payload_json(
        {
            "checkpoint_count": int(checkpoints[0]),
            "event_count": int(events[0]),
            "latest_observed_at": observed_at.isoformat(),
            "recommendation_count": int(recommendations[0]),
            "session": database.parent.name,
        }
    )
    return ResearchAgentEvidenceMaterial(
        family="day_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"day.session.{database.parent.name}",
        observed_at=observed_at,
        available_at=observed_at,
        market_id="us_equities",
        canonical_payload=payload,
    ).evidence()


__all__ = ("DaySourceAdapter", "MarketContextSourceAdapter", "OpportunitySourceAdapter")
