from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_primary_admission import (
    DaySourceAdmission,
    day_research_admission,
    market_context_admission,
    opportunity_admission,
)
from trading_agent.research_agent_source_common import (
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    canonical_payload_json,
    opportunity_candidate_subject_ref,
    require_private_source_file,
    require_source_boundary,
)
from trading_agent.signal_contract_models import OpportunitySnapshot


def archived_opportunity_evidence(
    snapshot: OpportunitySnapshot,
    now: dt.datetime,
) -> ResearchAgentEvidenceV1 | None:
    timestamps = (
        snapshot.observed_at,
        *(reference.observed_at for reference in snapshot.evidence_refs),
        *(coverage.observed_at for coverage in snapshot.source_coverage),
    )
    if any(timestamp > now for timestamp in timestamps) or opportunity_admission(
        snapshot, snapshot.observed_at
    ) is not None:
        return None
    source_key = f"opportunity.research_archive.{snapshot.opportunity_id}"
    return ResearchAgentEvidenceMaterial(
        family="opportunity_manager",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=source_key,
        observed_at=snapshot.observed_at,
        available_at=snapshot.observed_at,
        market_id=snapshot.strategy_lane.market_id.value,
        canonical_payload=_archive_payload(canonical_model_json(snapshot)),
        subject_refs=tuple(
            sorted(
                (
                    source_key,
                    *(opportunity_candidate_subject_ref(source_key, item.rank) for item in snapshot.candidates),
                )
            )
        ),
    ).evidence()


def archived_market_context_evidence(
    snapshot: MarketContextSnapshot,
    now: dt.datetime,
) -> ResearchAgentEvidenceV1 | None:
    timestamps = (snapshot.observed_at, *(coverage.observed_at for coverage in snapshot.coverage))
    if any(timestamp > now for timestamp in timestamps) or market_context_admission(
        snapshot, snapshot.observed_at
    ) is not None:
        return None
    return ResearchAgentEvidenceMaterial(
        family="market_context",
        trigger=ResearchAgentTriggerKind.MARKET_EVENT,
        source_key=f"market_context.research_archive.{snapshot.context_id}",
        observed_at=snapshot.observed_at,
        available_at=snapshot.observed_at,
        market_id=snapshot.market_id.value,
        canonical_payload=_archive_payload(canonical_model_json(snapshot)),
    ).evidence()


def archived_day_admission(
    database: Path,
    risk_screen: Path,
    now: dt.datetime,
) -> DaySourceAdmission | None:
    admission = day_research_admission(database, risk_screen, now)
    return admission if isinstance(admission, DaySourceAdmission) else None


def archived_day_evidence(
    admission: DaySourceAdmission,
    session: str,
) -> ResearchAgentEvidenceV1:
    evidence = ResearchAgentEvidenceMaterial(
        family="day_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"day.research_archive.{session}",
        observed_at=admission.observed_at,
        available_at=admission.observed_at,
        market_id="us_equities",
        canonical_payload=_archive_payload(admission.canonical_payload),
    ).evidence()
    references = tuple(sorted((evidence.payload_sha256, *admission.provenance_sha256)))
    return evidence.model_copy(update={"evidence_refs": references})


def archived_market_context_from_day(
    admission: DaySourceAdmission,
    session: str,
) -> ResearchAgentEvidenceV1:
    evidence = ResearchAgentEvidenceMaterial(
        family="market_context",
        trigger=ResearchAgentTriggerKind.MARKET_EVENT,
        source_key=f"market_context.research_archive.day.{session}",
        observed_at=admission.observed_at,
        available_at=admission.observed_at,
        market_id="cross_market",
        canonical_payload=_archive_payload(admission.canonical_payload),
    ).evidence()
    references = tuple(sorted((evidence.payload_sha256, *admission.provenance_sha256)))
    return evidence.model_copy(update={"evidence_refs": references})


def archived_market_context_from_latest_day(
    day_session_root: Path,
    now: dt.datetime,
) -> ResearchAgentEvidenceV1 | None:
    latest = _latest_archived_day(day_session_root, now)
    if latest is None:
        return None
    session, admission = latest
    return archived_market_context_from_day(admission, session)


def archived_swing_from_day(
    day_session_root: Path | None,
    now: dt.datetime,
) -> ResearchAgentEvidenceV1 | None:
    if day_session_root is None:
        return None
    latest = _latest_archived_day(day_session_root, now)
    if latest is None:
        return None
    session, admission = latest
    evidence = ResearchAgentEvidenceMaterial(
        family="swing_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"swing.research_archive.day.{session}",
        observed_at=admission.observed_at,
        available_at=admission.observed_at,
        market_id="us_equities",
        canonical_payload=_archive_payload(admission.canonical_payload),
    ).evidence()
    references = tuple(sorted((evidence.payload_sha256, *admission.provenance_sha256)))
    return evidence.model_copy(update={"evidence_refs": references})


def _latest_archived_day(
    day_session_root: Path,
    now: dt.datetime,
) -> tuple[str, DaySourceAdmission] | None:
    if not day_session_root.exists():
        return None
    require_source_boundary(day_session_root)
    sessions = tuple(
        sorted(
            (
                path
                for path in day_session_root.iterdir()
                if path.is_dir() and not path.is_symlink() and path.name.isdigit() and len(path.name) == 8
            ),
            key=lambda path: path.name,
        )[-32:]
    )
    for session in reversed(sessions):
        database = session / "paper_recommendations.sqlite3"
        risk_screen = session / "market_risk_screen.csv"
        if not database.exists() or not risk_screen.exists():
            continue
        require_private_source_file(database)
        require_private_source_file(risk_screen)
        admission = archived_day_admission(database, risk_screen, now)
        if admission is None:
            continue
        return session.name, admission
    return None


def _archive_payload(source_payload: str) -> str:
    return canonical_payload_json(
        {
            "research_only": True,
            "source_payload": json.loads(source_payload),
            "trading_authority": False,
        }
    )


__all__ = (
    "archived_day_admission",
    "archived_day_evidence",
    "archived_market_context_evidence",
    "archived_market_context_from_day",
    "archived_market_context_from_latest_day",
    "archived_opportunity_evidence",
    "archived_swing_from_day",
)
