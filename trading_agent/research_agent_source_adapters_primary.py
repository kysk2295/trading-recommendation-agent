from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal, Protocol, assert_never, final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_discovery_loop import (
    DayDiscoveryEvidenceView,
    DayDiscoveryTriggerKind,
    sanitize_day_discovery_feedback,
)
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
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_evidence_service import (
    KisKrMarketSessionGate,
    StrategyResearchEvidenceRejected,
)
from trading_agent.us_equity_calendar import NEW_YORK

KST = ZoneInfo("Asia/Seoul")


class PrimarySourcePaths(Protocol):
    market_context_root: Path
    day_session_root: Path
    kr_calendar_store: Path | None


@final
class OpportunitySourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: PrimarySourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        outbox = _latest_session_artifact(paths.day_session_root, "opportunities.v1.jsonl")
        if outbox is None:
            return _blocked_opportunity(primary_session_failure(now) or "snapshot_unavailable", now)
        try:
            require_source_boundary(outbox)
            snapshots = read_opportunity_snapshots(outbox)
        except (InvalidHermesProjectionSourceError, OSError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="opportunity_source_invalid") from None
        if not snapshots:
            return _blocked_opportunity("snapshot_unavailable", now)
        snapshot = max(snapshots, key=lambda item: item.observed_at)
        market_id = snapshot.strategy_lane.market_id
        session_failure = _market_session_failure(paths, market_id, now)
        failure = opportunity_admission(snapshot, now)
        if session_failure is None and _session_is_current(outbox.parent, now, market_id) and failure is None:
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
        if not paths.market_context_root.exists():
            archived = archived_market_context_from_latest_day(paths.day_session_root, now)
            return (
                (archived,)
                if archived is not None
                else _blocked_market_context(primary_session_failure(now) or "snapshot_unavailable", now)
            )
        require_source_boundary(paths.market_context_root)
        artifacts = tuple(sorted(paths.market_context_root.glob("*.market-context.json"))[-8:])
        if not artifacts:
            archived = archived_market_context_from_latest_day(paths.day_session_root, now)
            return (
                (archived,)
                if archived is not None
                else _blocked_market_context(primary_session_failure(now) or "snapshot_unavailable", now)
            )
        try:
            snapshots: list[MarketContextSnapshot] = []
            for artifact in artifacts:
                require_private_source_file(artifact)
                snapshots.append(MarketContextSnapshot.model_validate_json(artifact.read_text(encoding="utf-8")))
        except (InvalidResearchAgentSourceError, OSError, UnicodeError, ValidationError, ValueError):
            raise InvalidResearchAgentSourceError(reason="market_context_source_invalid") from None
        snapshot = max(snapshots, key=lambda item: item.observed_at)
        session_failure = _market_session_failure(paths, snapshot.market_id, now)
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
        discoveries_by_market: dict[str, ResearchAgentEvidenceV1] = {}
        for artifact in _day_discovery_artifacts(paths.day_session_root, now):
            try:
                evidence = _day_discovery_evidence(artifact, now)
            except InvalidResearchAgentSourceError as error:
                market_id = _day_discovery_artifact_market(artifact)
                evidence = _blocked_day(error.reason, now, market_id)[0]
            if evidence is not None:
                discoveries_by_market[evidence.market_id] = evidence
        discoveries = tuple(discoveries_by_market[key] for key in sorted(discoveries_by_market))
        recommendation = _day_recommendation_evidence(paths, sessions, session_failure, now)
        if discoveries and recommendation[0].source_key.startswith("day.blocked."):
            return discoveries
        return (*discoveries, *recommendation)


def _day_recommendation_evidence(
    paths: PrimarySourcePaths,
    sessions: tuple[Path, ...],
    session_failure: PrimaryAdmissionFailure | None,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
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
            if session_failure is not None or not _session_is_current(
                session,
                now,
                MarketId.US_EQUITIES,
            ):
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
                subject_refs=(f"day.session.{session.name}", *admitted.subject_refs),
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


def _day_discovery_artifacts(root: Path, now: dt.datetime) -> tuple[Path, ...]:
    names = (
        "day-discovery-evidence.v1.json",
        "day-discovery-evidence.kr_equities.v1.json",
        "day-discovery-evidence.us_equities.v1.json",
    )
    current_sessions = {
        now.astimezone(KST).strftime("%Y%m%d"),
        now.astimezone(NEW_YORK).strftime("%Y%m%d"),
    }
    return tuple(
        artifact
        for session in _latest_session_directories(root)
        if session.name in current_sessions
        for name in names
        if (artifact := session / name).exists()
    )


def _day_discovery_evidence(path: Path, now: dt.datetime) -> ResearchAgentEvidenceV1 | None:
    try:
        require_private_source_file(path)
        raw = path.read_text(encoding="utf-8")
        view = DayDiscoveryEvidenceView.model_validate_json(raw)
        if raw != canonical_model_json(view):
            raise InvalidResearchAgentSourceError(reason="day_discovery_source_noncanonical")
        if view.observed_at > now:
            return None
        if not _session_is_current(path.parent, now, view.market_id):
            raise InvalidResearchAgentSourceError(reason="day_discovery_source_not_current")
        feedback_path = path.with_name(path.name.replace("evidence", "feedback"))
        if feedback_path.exists():
            require_private_source_file(feedback_path)
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            if not isinstance(feedback, dict):
                raise InvalidResearchAgentSourceError(reason="day_discovery_feedback_invalid")
            view = view.model_copy(update={"feedback": sanitize_day_discovery_feedback(feedback)})
        payload = canonical_model_json(view)
        payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        trigger = {
            DayDiscoveryTriggerKind.COMPLETED_BAR: ResearchAgentTriggerKind.NEW_DATA,
            DayDiscoveryTriggerKind.POINT_IN_TIME_EVIDENCE: ResearchAgentTriggerKind.NEW_DATA,
            DayDiscoveryTriggerKind.TERMINAL_EVENT: ResearchAgentTriggerKind.EXPERIMENT_RESULT,
            DayDiscoveryTriggerKind.REVIEW_CLOSE: ResearchAgentTriggerKind.REVIEWER_FEEDBACK,
            DayDiscoveryTriggerKind.EXPLORATION_DUE: ResearchAgentTriggerKind.SCHEDULED_WAKE,
        }[view.trigger_kind]
        source_key = f"day.discovery.{view.market_id.value}.{payload_sha256[:24]}"
        return ResearchAgentEvidenceMaterial(
            family="day_trading",
            trigger=trigger,
            source_key=source_key,
            observed_at=view.observed_at,
            available_at=view.observed_at,
            market_id=view.market_id.value,
            canonical_payload=payload,
            subject_refs=(source_key,),
        ).evidence()
    except InvalidResearchAgentSourceError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidResearchAgentSourceError(reason="day_discovery_source_invalid") from None


def _session_is_current(session: Path, now: dt.datetime, market_id: MarketId) -> bool:
    zone = KST if market_id is MarketId.KR_EQUITIES else NEW_YORK
    current_name = now.astimezone(zone).strftime("%Y%m%d")
    return session.name == current_name


def _market_session_failure(
    paths: PrimarySourcePaths,
    market_id: MarketId,
    now: dt.datetime,
) -> PrimaryAdmissionFailure | None:
    if market_id is MarketId.US_EQUITIES:
        return primary_session_failure(now)
    if market_id is not MarketId.KR_EQUITIES or paths.kr_calendar_store is None:
        return PrimaryAdmissionFailure.SESSION_CLOSED
    try:
        KisKrMarketSessionGate(paths.kr_calendar_store).require_open("kr_equities", now)
    except StrategyResearchEvidenceRejected:
        return PrimaryAdmissionFailure.SESSION_CLOSED
    return None


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
    market_id: Literal["us_equities", "kr_equities"] = "us_equities",
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return _blocked(
        CapabilityEvidenceSpec(
            family="day_trading",
            source_key=f"day.blocked.{reason}",
            market_id=market_id,
        ),
        now,
    )


def _day_discovery_artifact_market(
    path: Path,
) -> Literal["us_equities", "kr_equities"]:
    if ".kr_equities." in path.name:
        return "kr_equities"
    return "us_equities"


def _blocked(
    spec: CapabilityEvidenceSpec,
    now: dt.datetime,
) -> tuple[ResearchAgentEvidenceV1, ...]:
    return (capability_evidence(spec, now),)


def bounded_day_discovery_feedback(payload: dict[str, object]) -> str:
    return canonical_model_json(sanitize_day_discovery_feedback(payload))


__all__ = (
    "DaySourceAdapter",
    "MarketContextSourceAdapter",
    "OpportunitySourceAdapter",
    "PrimarySourcePaths",
    "bounded_day_discovery_feedback",
)
