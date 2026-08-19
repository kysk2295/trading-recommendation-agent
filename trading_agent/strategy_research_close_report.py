from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Final, assert_never

from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, HermesProjectionResult, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.strategy_research_catalog import STRATEGY_RESEARCH_CATALOG, StrategyResearchIdentity
from trading_agent.strategy_research_forward_observations import ForwardResearchObservation
from trading_agent.strategy_research_ledger import AgentResearchStateEvent
from trading_agent.strategy_research_methodologies import strategy_research_methodology
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import AttemptStatus, ResearchAgentId, TerminalOutcome
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_REPORT_DELAY: Final = dt.timedelta(minutes=15)
_SOURCE_PREFIX: Final = "strategy-research-close-report"


class StrategyResearchCloseReportError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CloseReportSnapshot:
    reader: ExperimentLedgerReader
    manifests: tuple[PreregistrationManifest, ...]
    session_date: dt.date
    forward_observations: tuple[ForwardResearchObservation, ...] = ()


def project_strategy_research_close_report(
    reader: ExperimentLedgerReader,
    writer: HermesDeliveryWriter,
    now: dt.datetime,
    *,
    forward_observations: tuple[ForwardResearchObservation, ...] = (),
) -> HermesProjectionResult:
    session = _latest_completed_session(now)
    if session is None:
        return HermesProjectionResult(examined=0, inserted=0, replayed=0)
    session_date, occurred_at = session
    source_event_id = f"{_SOURCE_PREFIX}:{session_date.isoformat()}"
    snapshot = CloseReportSnapshot(
        reader,
        reader.strategy_research_preregistrations(),
        session_date,
        forward_observations,
    )
    record = _report_record(snapshot, source_event_id, occurred_at)
    return project_outcomes((record,), writer)


def _latest_completed_session(now: dt.datetime) -> tuple[dt.date, dt.datetime] | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise StrategyResearchCloseReportError("close_report_now_must_be_aware")
    local_now = now.astimezone(NEW_YORK)
    current_bounds = regular_session_bounds(local_now.date())
    if current_bounds is not None and local_now < current_bounds[1] + _REPORT_DELAY:
        return None
    for days_ago in range(14):
        session_date = local_now.date() - dt.timedelta(days=days_ago)
        bounds = regular_session_bounds(session_date)
        if bounds is None:
            continue
        occurred_at = bounds[1] + _REPORT_DELAY
        if local_now >= occurred_at:
            return session_date, occurred_at
    return None


def _report_record(
    snapshot: CloseReportSnapshot,
    source_event_id: str,
    occurred_at: dt.datetime,
) -> HermesProjectionRecord:
    lines = tuple(_agent_line(snapshot, identity) for identity in STRATEGY_RESEARCH_CATALOG)
    rendered_text = redact_outbound_text(
        "\n".join(
            (
                f"Six-agent research close {snapshot.session_date.isoformat()}. Research-only; "
                "profitability claim: false; "
                "order authority: false; trading authority: false.",
                *lines,
            )
        ),
        max_chars=4096,
    ).strip()
    require_safe_outbound_text(rendered_text)
    evidence_refs = tuple(
        sorted(
            {
                ref
                for identity in STRATEGY_RESEARCH_CATALOG
                for ref in _session_evidence_refs(snapshot, identity.agent_id)
            }
        )
    )
    return HermesProjectionRecord(
        source_event_id=source_event_id,
        root_source_event_id=None,
        kind=HermesDeliveryKind.DAILY_SUMMARY,
        market_id="us_equities",
        agent_family="strategy_research",
        lane_id=None,
        strategy_version=None,
        instrument_id=None,
        occurred_at=occurred_at,
        status="six_agent_state",
        evidence_refs=evidence_refs,
        rendered_text=rendered_text,
        payload_sha256=hashlib.sha256(rendered_text.encode()).hexdigest(),
    )


def _agent_line(
    snapshot: CloseReportSnapshot,
    identity: StrategyResearchIdentity,
) -> str:
    owned = tuple(item for item in snapshot.manifests if item.hypothesis.agent_id is identity.agent_id)
    active = max(owned, key=lambda item: item.hypothesis.created_at) if owned else None
    history = snapshot.reader.strategy_research_agent_state(identity.agent_id)
    state = history[-1] if history else None
    feedback = snapshot.reader.strategy_research_feedback(identity.agent_id)
    result = max(feedback, key=lambda item: item.evaluated_at) if feedback else None
    attempts = () if active is None else snapshot.reader.strategy_research_attempts(active.hypothesis.hypothesis_id)
    stage = _stage(active, state, result)
    evidence = _session_evidence_refs(snapshot, identity.agent_id)
    hypothesis_id = "none" if active is None else active.hypothesis.hypothesis_id
    evidence_mode = "none" if active is None else active.hypothesis.evidence_use.value
    terminal = _terminal_summary(result)
    next_maturity = None if state is None else state.next_maturity_at
    if next_maturity is None and active is not None and result is None:
        next_maturity = active.hypothesis.target_matures_at
    maturity = "none" if next_maturity is None else next_maturity.isoformat()
    waiting_reason = _waiting_reason(active, state, result)
    next_test = _next_test(identity.agent_id, result)
    shadow = _shadow_summary(history)
    forward_samples = sum(
        item.agent_id is identity.agent_id and item.cluster_key == snapshot.session_date.isoformat()
        for item in snapshot.forward_observations
    )
    return (
        f"owner={identity.agent_id.value}; identity={identity.identity}; methodology={identity.methodology}; "
        f"evidence={','.join(evidence) if evidence else 'none'}; evidence_mode={evidence_mode}; "
        f"hypothesis={hypothesis_id}; summary={identity.output_contract}; stage={stage}; "
        f"attempts={len(attempts)}[{_attempt_counts(attempts)}]; terminal={terminal}; shadow={shadow}; "
        f"forward_samples={forward_samples}; "
        f"waiting_reason={waiting_reason}; next_maturity_at={maturity}; next_test={next_test}."
    )


def _session_evidence_refs(
    snapshot: CloseReportSnapshot,
    agent_id: ResearchAgentId,
) -> tuple[str, ...]:
    hypothesis_refs = tuple(
        ref.evidence_id
        for manifest in snapshot.manifests
        if manifest.hypothesis.agent_id is agent_id
        for ref in manifest.hypothesis.source_refs
        if ref.available_at.astimezone(NEW_YORK).date() == snapshot.session_date
    )
    state_refs = tuple(
        ref
        for event in snapshot.reader.strategy_research_agent_state(agent_id)
        if event.last_available_at.astimezone(NEW_YORK).date() == snapshot.session_date
        for ref in event.evidence_refs
    )
    return tuple(sorted(set(hypothesis_refs + state_refs)))


def _stage(
    active: PreregistrationManifest | None,
    state: AgentResearchStateEvent | None,
    result: TerminalResearchResult | None,
) -> str:
    if state is not None:
        return state.state
    if result is not None:
        return result.outcome.value
    return "waiting_evidence" if active is None else "preregistered"


def _waiting_reason(
    active: PreregistrationManifest | None,
    state: AgentResearchStateEvent | None,
    result: TerminalResearchResult | None,
) -> str:
    if state is not None:
        return state.reason
    if result is not None:
        match result.outcome:
            case TerminalOutcome.SUPPORTED:
                return "awaiting_strictly_future_shadow_evidence"
            case TerminalOutcome.REFUTED | TerminalOutcome.INCONCLUSIVE:
                return "terminal_lineage_closed"
            case unreachable:
                assert_never(unreachable)
    return "awaiting_source_bound_evidence" if active is None else "awaiting_attempt_or_target_maturity"


def _terminal_summary(result: TerminalResearchResult | None) -> str:
    if result is None:
        return "none;limitations=no_sanitized_terminal_result"
    reasons = ",".join(reason.value for reason in result.reason_codes)
    return f"{result.outcome.value};result_ref={result.result_id};limitations={reasons}"


def _shadow_summary(history: tuple[AgentResearchStateEvent, ...]) -> str:
    state = next((event for event in reversed(history) if event.shadow_observation_id is not None), None)
    if state is None:
        return "none"
    gate = "met_owner_approval_required" if state.shadow_information_sufficient else "pending"
    return f"{state.shadow_sample_count}/{state.shadow_sample_target};information_gate={gate}"


def _next_test(agent_id: ResearchAgentId, result: TerminalResearchResult | None) -> str:
    if result is None:
        return strategy_research_methodology(agent_id).next_test_policy
    match result.outcome:
        case TerminalOutcome.SUPPORTED:
            return strategy_research_methodology(agent_id).next_test_policy
        case TerminalOutcome.REFUTED:
            return "preregister_changed_method_as_new_lineage"
        case TerminalOutcome.INCONCLUSIVE:
            return "wait_for_named_evidence_or_maturity"
        case unreachable:
            assert_never(unreachable)


def _attempt_counts(attempts: tuple[ResearchAttempt, ...]) -> str:
    counts = Counter(item.status for item in attempts)
    return ",".join(f"{status.value}:{counts[status]}" for status in AttemptStatus)


__all__ = ("StrategyResearchCloseReportError", "project_strategy_research_close_report")
