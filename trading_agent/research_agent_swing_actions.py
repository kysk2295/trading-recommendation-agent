from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.research_agent_actions import InvalidResearchAgentActionError, ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ResearchAgentDecisionKind,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.swing_shadow_cli_files import InvalidSwingShadowCliTargetError, load_private_swing_sources
from trading_agent.swing_shadow_engine import InvalidSwingShadowEngineError, advance_swing_shadow_session
from trading_agent.swing_shadow_store import (
    InvalidSwingShadowLedgerError,
    ShadowEventKind,
    SwingShadowEvent,
    SwingShadowStore,
)

_TERMINAL = {
    ShadowEventKind.STOPPED,
    ShadowEventKind.TARGETED,
    ShadowEventKind.TIME_EXIT,
    ShadowEventKind.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class SwingResearchActionExecutor:
    shadow_database: Path

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        if context.cycle.agent_family_id != "swing_trading":
            raise InvalidResearchAgentActionError(reason="action_family_identity_mismatch")
        if context.decision.primary_decision is not ResearchAgentDecisionKind.REVIEW_OPEN_STATE:
            raise InvalidResearchAgentActionError(reason="prose_only_result")
        cycle_evidence = tuple(
            evidence for evidence in context.evidence if evidence.evidence_id == context.cycle.evidence_id
        )
        if len(cycle_evidence) != 1:
            raise InvalidResearchAgentActionError(reason="action_evidence_identity_mismatch")
        if cycle_evidence[0].source_key.startswith("swing.research_archive.day."):
            return _archive_open_state_unavailable(context)
        evidence = _selected_evidence(context)
        signal, evidence_events = _selected_artifacts(evidence)
        try:
            store = SwingShadowStore(self.shadow_database)
            matching = tuple(item for item in store.signals() if item.signal_id == signal.signal_id)
            events = store.events(signal.signal_id)
            if len(matching) != 1 or matching[0] != signal or events[: len(evidence_events)] != evidence_events:
                raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
            appended = self._advance(store, signal, events)
            current = store.events(signal.signal_id)
        except InvalidResearchAgentActionError:
            raise
        except (
            InvalidSwingShadowCliTargetError,
            InvalidSwingShadowEngineError,
            InvalidSwingShadowLedgerError,
            OSError,
        ):
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None
        if not current:
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        latest = current[-1]
        if not appended:
            return _unchanged_result(context, signal, latest)
        return _completed_result(context, evidence.payload_sha256, signal, latest)

    def _advance(
        self,
        store: SwingShadowStore,
        signal: TradeSignalEnvelope,
        events: tuple[SwingShadowEvent, ...],
    ) -> tuple[SwingShadowEvent, ...]:
        latest = events[-1]
        if latest.kind in _TERMINAL:
            return ()
        sources = tuple(
            source
            for source in load_private_swing_sources(self.shadow_database.parent)
            if signal.symbol in source.symbols and source.observed_at > latest.observed_at
        )
        if not sources:
            return ()
        appended: list[SwingShadowEvent] = []
        with store.writer() as writer:
            for source in sources:
                appended.extend(advance_swing_shadow_session(writer, source=source))
                if appended and appended[-1].kind in _TERMINAL:
                    break
        return tuple(appended)


def _selected_evidence(context: ResearchAgentActionContext) -> ResearchAgentEvidenceV1:
    selected = set(context.decision.subject_refs)
    matches = tuple(
        evidence
        for evidence in context.evidence
        if selected.intersection((str(evidence.evidence_id), *evidence.subject_refs))
    )
    if len(matches) != 1 or matches[0].bounded_payload_json is None:
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
    return matches[0]


def _selected_artifacts(evidence: ResearchAgentEvidenceV1):
    try:
        payload = json.loads(evidence.bounded_payload_json or "")
        if isinstance(payload, dict) and "source_payload" in payload:
            if payload.get("research_only") is not True or payload.get("trading_authority") is not False:
                raise ValueError
            payload = payload["source_payload"]
        if not isinstance(payload, dict) or set(payload) != {"events", "signal"}:
            raise ValueError
        signal = TradeSignalEnvelope.model_validate(payload["signal"])
        events = tuple(SwingShadowEvent.model_validate(event) for event in payload["events"])
        if not events or any(event.signal_id != signal.signal_id for event in events):
            raise ValueError
        return signal, events
    except (TypeError, ValidationError, ValueError):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None


def _completed_result(
    context: ResearchAgentActionContext,
    evidence_sha: str,
    signal: TradeSignalEnvelope,
    event: SwingShadowEvent,
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="swing_trading",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.COMPLETED,
        question=context.decision.question,
        summary=_summary(signal, event),
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=tuple(sorted((evidence_sha, event.source_key))),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def _unchanged_result(
    context: ResearchAgentActionContext,
    signal: TradeSignalEnvelope,
    event: SwingShadowEvent,
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="swing_trading",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary=_summary(signal, event),
        reason="shadow_state_unchanged",
        continuation="Wait for a newer completed Swing daily-source artifact.",
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def _archive_open_state_unavailable(context: ResearchAgentActionContext) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="swing_trading",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="Archived Day research evidence does not contain a Swing open state.",
        reason="swing_archive_open_state_unavailable",
        continuation="Wait for a verified Swing signal and event artifact.",
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def _summary(signal: TradeSignalEnvelope, event: SwingShadowEvent) -> str:
    return (
        f"signal={signal.signal_id},symbol={signal.symbol},entry={signal.entry_price},"
        f"stop={signal.stop_price},state={event.kind.value},event={event.event_id},"
        f"observed_at={event.observed_at.isoformat()},price={event.price}"
    )


__all__ = ("SwingResearchActionExecutor",)
