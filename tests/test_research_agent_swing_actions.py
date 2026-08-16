from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from trading_agent.data_capability_models import DataSourceId
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_source_adapters_research import SwingSourceAdapter
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial, canonical_payload_json
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_swing_actions import SwingResearchActionExecutor
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_cli_files import write_private_swing_source
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowStore
from trading_agent.us_equity_calendar import regular_session_bounds

SIGNAL_SESSION = dt.date(2026, 7, 15)


def test_swing_evidence_exposes_signal_and_immutable_event_subjects(tmp_path: Path) -> None:
    paths, signal = _seed_signal(tmp_path)

    evidence = SwingSourceAdapter().collect(paths, _observed_after_close(SIGNAL_SESSION))[0]

    assert evidence.source_key in evidence.subject_refs
    assert any(subject.startswith("swing_signal.") for subject in evidence.subject_refs)
    assert any(subject.startswith("swing_event.") for subject in evidence.subject_refs)
    assert signal.symbol in (evidence.bounded_payload_json or "")


def test_review_open_state_resolves_latest_existing_event_without_mutation(tmp_path: Path) -> None:
    paths, _ = _seed_signal(tmp_path)
    evidence = SwingSourceAdapter().collect(paths, _observed_after_close(SIGNAL_SESSION))[0]

    result = SwingResearchActionExecutor(paths.swing_shadow_database).execute(_context(evidence))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "shadow_state_unchanged"
    assert len(SwingShadowStore(paths.swing_shadow_database).events(_signal_id(paths))) == 1


def test_review_open_state_accepts_research_archive_envelope(tmp_path: Path) -> None:
    paths, _ = _seed_signal(tmp_path)
    evidence = SwingSourceAdapter().collect(paths, _observed_after_close(SIGNAL_SESSION))[0]
    archive_source = f"swing.research_archive.{SIGNAL_SESSION:%Y%m%d}"
    archived = ResearchAgentEvidenceMaterial(
        family="swing_trading",
        trigger=evidence.trigger_kind,
        source_key=archive_source,
        observed_at=evidence.observed_at,
        available_at=evidence.available_at,
        market_id=evidence.market_id,
        canonical_payload=canonical_payload_json(
            {
                "research_only": True,
                "source_payload": json.loads(evidence.bounded_payload_json or "null"),
                "trading_authority": False,
            }
        ),
        subject_refs=tuple(sorted((archive_source, *evidence.subject_refs))),
    ).evidence()

    result = SwingResearchActionExecutor(paths.swing_shadow_database).execute(_context(archived))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "shadow_state_unchanged"


def test_archived_day_fallback_without_swing_state_is_no_action(tmp_path: Path) -> None:
    paths, _ = _seed_signal(tmp_path)
    source_key = "swing.research_archive.day.20260715"
    evidence = ResearchAgentEvidenceMaterial(
        family="swing_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=source_key,
        observed_at=_observed_after_close(SIGNAL_SESSION),
        available_at=_observed_after_close(SIGNAL_SESSION),
        market_id="us_equities",
        canonical_payload=canonical_payload_json(
            {
                "research_only": True,
                "source_payload": {"session": "20260715"},
                "trading_authority": False,
            }
        ),
    ).evidence()

    result = SwingResearchActionExecutor(paths.swing_shadow_database).execute(_context(evidence))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "swing_archive_open_state_unavailable"


def test_archived_day_fallback_uses_cycle_evidence_when_decision_selects_open_work(tmp_path: Path) -> None:
    paths, _ = _seed_signal(tmp_path)
    evidence = ResearchAgentEvidenceMaterial(
        family="swing_trading",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key="swing.research_archive.day.20260715",
        observed_at=_observed_after_close(SIGNAL_SESSION),
        available_at=_observed_after_close(SIGNAL_SESSION),
        market_id="us_equities",
        canonical_payload=canonical_payload_json({"session": "20260715"}),
    ).evidence()
    context = _context(evidence)
    work = ResearchAgentOpenWorkV1(
        work_id="swing-open-work-archive-001",
        cycle_id=context.cycle.cycle_id,
        agent_family_id="swing_trading",
        state=ResearchAgentOpenWorkState.OPEN,
        evidence_refs=evidence.evidence_refs,
        next_wake_at=context.observed_at,
        updated_at=context.observed_at,
    )
    decision = context.decision.model_copy(update={"subject_refs": (work.work_id,)})
    selected_work = ResearchAgentActionContext(
        context.cycle,
        context.evidence,
        (work,),
        decision,
        context.observed_at,
    )

    result = SwingResearchActionExecutor(paths.swing_shadow_database).execute(selected_work)

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "swing_archive_open_state_unavailable"


def test_completed_daily_source_advances_existing_signal_stop_first(tmp_path: Path) -> None:
    paths, signal = _seed_signal(tmp_path)
    evidence = SwingSourceAdapter().collect(paths, _observed_after_close(SIGNAL_SESSION))[0]
    entry_session = _next_session(SIGNAL_SESSION)
    source = _session_source(entry_session)
    _ = write_private_swing_source(paths.swing_shadow_database.parent, source)

    result = SwingResearchActionExecutor(paths.swing_shadow_database).execute(_context(evidence))
    events = SwingShadowStore(paths.swing_shadow_database).events(signal.signal_id)
    feedback = SwingSourceAdapter().collect(paths, result.occurred_at)[0]

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert tuple(event.kind for event in events) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.STOPPED,
    )
    assert "state=stopped" in result.summary
    assert events[-1].source_key in result.artifact_refs
    assert feedback.evidence_id != evidence.evidence_id
    assert '"kind":"stopped"' in (feedback.bounded_payload_json or "")


def test_replay_does_not_duplicate_swing_event(tmp_path: Path) -> None:
    paths, signal = _seed_signal(tmp_path)
    evidence = SwingSourceAdapter().collect(paths, _observed_after_close(SIGNAL_SESSION))[0]
    _ = write_private_swing_source(paths.swing_shadow_database.parent, _session_source(_next_session(SIGNAL_SESSION)))
    executor = SwingResearchActionExecutor(paths.swing_shadow_database)
    first = executor.execute(_context(evidence))
    refreshed = SwingSourceAdapter().collect(paths, first.occurred_at)[0]

    replay = executor.execute(_context(refreshed))

    assert replay.status is ResearchAgentResultStatus.NO_ACTION
    assert replay.reason == "shadow_state_unchanged"
    assert len(SwingShadowStore(paths.swing_shadow_database).events(signal.signal_id)) == 3


def _paths(tmp_path: Path) -> ResearchAgentSourcePaths:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market-context",
        day_session_root=outputs / "day",
        swing_shadow_database=outputs / "swing" / "shadow.sqlite3",
        swing_review_database=outputs / "swing" / "review.sqlite3",
        experiment_ledger=outputs / "experiment.sqlite3",
        lane_review_database=outputs / "review.sqlite3",
    )


def _seed_signal(tmp_path: Path):
    paths = _paths(tmp_path)
    source = _signal_source()
    signal = project_new_high_rvol_signals(source)[0]
    store = SwingShadowStore(paths.swing_shadow_database)
    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=source, signals=(signal,))
    return paths, signal


def _signal_id(paths: ResearchAgentSourcePaths) -> str:
    return SwingShadowStore(paths.swing_shadow_database).signals()[0].signal_id


def _context(evidence) -> ResearchAgentActionContext:
    now = evidence.observed_at + dt.timedelta(minutes=1)
    cycle = ResearchAgentCycleV1(
        cycle_id=CycleId("a" * 64),
        evidence_id=evidence.evidence_id,
        action_request_id=ActionId("b" * 64),
        agent_family_id="swing_trading",
        market_id="us_equities",
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=now,
    )
    decision = ResearchAgentDecisionV1(
        decision_id=DecisionId("c" * 64),
        cycle_id=cycle.cycle_id,
        agent_family_id="swing_trading",
        primary_decision=ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
        requested_action=ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
        question="What is the latest existing Swing shadow state?",
        summary="Resolve or advance only an existing shadow signal.",
        subject_refs=(evidence.source_key,),
        evidence_refs=evidence.evidence_refs,
        decided_at=now,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-v1",
        prompt_sha256="d" * 64,
        response_sha256="e" * 64,
    )
    return ResearchAgentActionContext(cycle, (evidence,), (), decision, now)


def _signal_source() -> SwingDailySource:
    sessions = _following_sessions(SIGNAL_SESSION, count=21, backwards=True)
    observed_at = _observed_after_close(SIGNAL_SESSION)
    bars = tuple(
        SwingDailyBar(
            symbol="ACME",
            session_date=session_date,
            observed_at=observed_at,
            open=Decimal("10"),
            high=Decimal("15.2") if index == len(sessions) - 1 else Decimal("10.2"),
            low=Decimal("9.9"),
            close=Decimal("15") if index == len(sessions) - 1 else Decimal("10"),
            volume=200_000 if index == len(sessions) - 1 else 100_000,
        )
        for index, session_date in enumerate(sessions)
    )
    return SwingDailySource(
        session_date=SIGNAL_SESSION,
        observed_at=observed_at,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="fixture-universe-v1",
        symbols=("ACME",),
        bars=bars,
    )


def _session_source(session_date: dt.date) -> SwingDailySource:
    observed_at = _observed_after_close(session_date)
    return SwingDailySource(
        session_date=session_date,
        observed_at=observed_at,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="fixture-universe-v1",
        symbols=("ACME",),
        bars=(
            SwingDailyBar(
                symbol="ACME",
                session_date=session_date,
                observed_at=observed_at,
                open=Decimal("15.10"),
                high=Decimal("18"),
                low=Decimal("13"),
                close=Decimal("15"),
                volume=100_000,
            ),
        ),
    )


def _observed_after_close(session_date: dt.date) -> dt.datetime:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    return bounds[1] + dt.timedelta(minutes=5)


def _next_session(session_date: dt.date) -> dt.date:
    return _following_sessions(session_date, count=1)[0]


def _following_sessions(
    session_date: dt.date,
    *,
    count: int,
    backwards: bool = False,
) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = session_date if backwards else session_date + dt.timedelta(days=1)
    direction = -1 if backwards else 1
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current += dt.timedelta(days=direction)
    return tuple(reversed(sessions)) if backwards else tuple(sessions)
