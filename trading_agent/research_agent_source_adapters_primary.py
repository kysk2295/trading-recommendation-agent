from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Protocol, assert_never, final

from pydantic import ValidationError

from trading_agent.hermes_delivery_projection import (
    InvalidHermesProjectionSourceError,
    read_opportunity_snapshots,
)
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_primary_admission import (
    DaySourceAdmission,
    PrimaryAdmissionFailure,
    day_source_admission,
    market_context_admission,
    opportunity_admission,
    primary_session_failure,
)
from trading_agent.research_agent_source_archives import (
    archived_day_admission,
    archived_day_evidence,
    archived_market_context_evidence,
    archived_market_context_from_latest_day,
    archived_opportunity_evidence,
)
from trading_agent.research_agent_source_common import (
    CapabilityEvidenceSpec,
    InvalidResearchAgentSourceError,
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    capability_evidence,
    opportunity_candidate_subject_ref,
    require_private_source_file,
    require_source_boundary,
)
from trading_agent.us_equity_calendar import NEW_YORK


class PrimarySourcePaths(Protocol):
    market_context_root: Path
    day_session_root: Path


@final
class OpportunitySourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: PrimarySourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        session_failure = primary_session_failure(now)
        outbox = _latest_session_artifact(paths.day_session_root, "opportunities.v1.jsonl")
        if outbox is None:
            return _blocked_opportunity(session_failure or "snapshot_unavailable", now)
        try:
            require_source_boundary(outbox)
            snapshots = read_opportunity_snapshots(outbox)
        except (InvalidHermesProjectionSourceError, OSError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="opportunity_source_invalid") from None
        if not snapshots:
            return _blocked_opportunity("snapshot_unavailable", now)
        snapshot = max(snapshots, key=lambda item: item.observed_at)
        failure = opportunity_admission(snapshot, now)
        if session_failure is None and _session_is_current(outbox.parent, now) and failure is None:
            source_key = f"opportunity.{snapshot.opportunity_id}"
            return (
                ResearchAgentEvidenceMaterial(
                    family="opportunity_manager",
                    trigger=ResearchAgentTriggerKind.NEW_DATA,
                    source_key=source_key,
                    observed_at=snapshot.observed_at,
                    available_at=snapshot.observed_at,
                    market_id=snapshot.strategy_lane.market_id.value,
                    canonical_payload=canonical_model_json(snapshot),
                    subject_refs=tuple(
                        sorted(
                            (
                                source_key,
                                *(
                                    opportunity_candidate_subject_ref(source_key, item.rank)
                                    for item in snapshot.candidates
                                ),
                            )
                        )
                    ),
                ).evidence(),
            )
        archived = archived_opportunity_evidence(snapshot, now)
        if archived is not None:
            return (archived,)
        return _blocked_opportunity(failure or session_failure or PrimaryAdmissionFailure.PRIOR_DATE, now)


@final
class MarketContextSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: PrimarySourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        session_failure = primary_session_failure(now)
        if not paths.market_context_root.exists():
            archived = archived_market_context_from_latest_day(paths.day_session_root, now)
            return (archived,) if archived is not None else _blocked_market_context(
                session_failure or "snapshot_unavailable", now
            )
        require_source_boundary(paths.market_context_root)
        artifacts = tuple(sorted(paths.market_context_root.glob("*.market-context.json"))[-8:])
        if not artifacts:
            archived = archived_market_context_from_latest_day(paths.day_session_root, now)
            return (archived,) if archived is not None else _blocked_market_context(
                session_failure or "snapshot_unavailable", now
            )
        try:
            snapshots: list[MarketContextSnapshot] = []
            for artifact in artifacts:
                require_private_source_file(artifact)
                snapshots.append(MarketContextSnapshot.model_validate_json(artifact.read_text(encoding="utf-8")))
        except (InvalidResearchAgentSourceError, OSError, UnicodeError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="market_context_source_invalid") from None
        snapshot = max(snapshots, key=lambda item: item.observed_at)
        failure = market_context_admission(snapshot, now)
        if session_failure is None and failure is None:
            return (
                ResearchAgentEvidenceMaterial(
                    family="market_context",
                    trigger=ResearchAgentTriggerKind.MARKET_EVENT,
                    source_key=f"market_context.{snapshot.context_id}",
                    observed_at=snapshot.observed_at,
                    available_at=snapshot.observed_at,
                    market_id=snapshot.market_id.value,
                    canonical_payload=canonical_model_json(snapshot),
                ).evidence(),
            )
        archived = archived_market_context_evidence(snapshot, now)
        if archived is not None:
            return (archived,)
        return _blocked_market_context(failure or session_failure or PrimaryAdmissionFailure.STALE, now)


@final
class DaySourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: PrimarySourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        session_failure = primary_session_failure(now)
        sessions = _latest_session_directories(paths.day_session_root)
        if not sessions:
            return _blocked_day(session_failure or "source_pair_unavailable", now)
        session = next(
            (
                candidate
                for candidate in reversed(sessions)
                if (candidate / "paper_recommendations.sqlite3").exists()
                and (candidate / "market_risk_screen.csv").exists()
            ),
            sessions[-1],
        )
        database = session / "paper_recommendations.sqlite3"
        risk_screen = session / "market_risk_screen.csv"
        if not database.exists() or not risk_screen.exists():
            return _blocked_day("source_pair_unavailable", now)
        try:
            require_private_source_file(database)
            require_private_source_file(risk_screen)
            admission = day_source_admission(database, risk_screen, now)
            archived = archived_day_admission(database, risk_screen, now)
        except (InvalidResearchAgentSourceError, OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidResearchAgentSourceError(reason="day_source_invalid") from None
        match admission:
            case PrimaryAdmissionFailure() as failure:
                if archived is not None:
                    return (archived_day_evidence(archived, session.name),)
                return _blocked_day(failure, now)
            case DaySourceAdmission() as admitted:
                if session_failure is not None or not _session_is_current(session, now):
                    if archived is not None:
                        return (archived_day_evidence(archived, session.name),)
                    return _blocked_day(session_failure or PrimaryAdmissionFailure.PRIOR_DATE, now)
                evidence = ResearchAgentEvidenceMaterial(
                    family="day_trading",
                    trigger=ResearchAgentTriggerKind.NEW_DATA,
                    source_key=f"day.session.{session.name}",
                    observed_at=admitted.observed_at,
                    available_at=admitted.observed_at,
                    market_id="us_equities",
                    canonical_payload=admitted.canonical_payload,
                ).evidence()
                references = tuple(sorted((evidence.payload_sha256, *admitted.provenance_sha256)))
                return (evidence.model_copy(update={"evidence_refs": references}),)
            case unreachable:
                assert_never(unreachable)


def _latest_session_directories(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    require_source_boundary(root)
    sessions = tuple(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and len(path.name) == 8 and path.name.isdigit()
    )
    return tuple(sorted(sessions, key=lambda path: path.name)[-32:])


def _latest_session_artifact(root: Path, name: str) -> Path | None:
    artifacts = tuple(session / name for session in _latest_session_directories(root) if (session / name).exists())
    return None if not artifacts else artifacts[-1]


def _session_is_current(session: Path, now: dt.datetime) -> bool:
    current_name = now.astimezone(NEW_YORK).strftime("%Y%m%d")
    return session.name == current_name


def _blocked_opportunity(
    reason: PrimaryAdmissionFailure | str,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return _blocked(
        CapabilityEvidenceSpec(
            family="opportunity_manager",
            source_key=f"opportunity.blocked.{reason}",
            market_id="none",
        ),
        now,
    )


def _blocked_market_context(
    reason: PrimaryAdmissionFailure | str,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return _blocked(
        CapabilityEvidenceSpec(
            family="market_context",
            source_key=f"market_context.blocked.{reason}",
            market_id="cross_market",
        ),
        now,
    )


def _blocked_day(
    reason: PrimaryAdmissionFailure | str,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return _blocked(
        CapabilityEvidenceSpec(
            family="day_trading",
            source_key=f"day.blocked.{reason}",
            market_id="us_equities",
        ),
        now,
    )


def _blocked(
    spec: CapabilityEvidenceSpec,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return (capability_evidence(spec, now),)


__all__ = (
    "DaySourceAdapter",
    "MarketContextSourceAdapter",
    "OpportunitySourceAdapter",
    "PrimarySourcePaths",
)
