from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.researcher_agent import ProposedHypothesis

_WHITESPACE: Final = re.compile(r"\s+")


class ObjectionKind(StrEnum):
    REDUNDANCY = "redundancy"
    FREE_PARAMS = "free_params"
    SOURCE_FIDELITY = "source_fidelity"
    MECHANISM = "mechanism"


class Severity(StrEnum):
    BLOCKING = "blocking"
    CAVEAT = "caveat"


@dataclass(frozen=True, slots=True)
class Objection:
    kind: ObjectionKind
    severity: Severity
    evidence: str


@dataclass(frozen=True, slots=True)
class CritiqueReport:
    objections: tuple[Objection, ...]

    @property
    def is_blocked(self) -> bool:
        return any(objection.severity is Severity.BLOCKING for objection in self.objections)


class HypothesisCritic(Protocol):
    def critique(
        self,
        proposal: ProposedHypothesis,
        ledger: ExperimentLedgerReader,
    ) -> CritiqueReport: ...


@dataclass(frozen=True, slots=True)
class DeterministicHypothesisCritic:
    max_free_parameters: int

    def critique(
        self,
        proposal: ProposedHypothesis,
        ledger: ExperimentLedgerReader,
    ) -> CritiqueReport:
        objections: list[Objection] = []
        if _repeats_rejected_hypothesis(proposal, ledger):
            objections.append(
                Objection(ObjectionKind.REDUNDANCY, Severity.BLOCKING, "rejected_scope_and_text_match")
            )
        if len(proposal.strategy_draft.free_parameters) > self.max_free_parameters:
            objections.append(
                Objection(
                    ObjectionKind.FREE_PARAMS,
                    Severity.BLOCKING,
                    f"free_parameters:{len(proposal.strategy_draft.free_parameters)}>{self.max_free_parameters}",
                )
            )
        return CritiqueReport(tuple(objections))


def _repeats_rejected_hypothesis(
    proposal: ProposedHypothesis,
    ledger: ExperimentLedgerReader,
) -> bool:
    expected_text = _canonical_text(proposal.card.hypothesis.hypothesis)
    expected_scope = proposal.card.hypothesis.experiment_scope
    versions = ledger.strategy_versions()
    rejected_hypothesis_ids = {
        version.registration.hypothesis_id
        for version in versions
        if _is_rejected(version.registration.strategy_version, ledger)
    }
    return any(
        card.card.hypothesis.hypothesis_id in rejected_hypothesis_ids
        and card.card.hypothesis.experiment_scope.scope_kind is expected_scope.scope_kind
        and card.card.hypothesis.experiment_scope.primary_lane is expected_scope.primary_lane
        and card.card.hypothesis.experiment_scope.lanes == expected_scope.lanes
        and _canonical_text(card.card.hypothesis.hypothesis) == expected_text
        for card in ledger.research_hypothesis_cards()
    )


def _is_rejected(strategy_version: str, ledger: ExperimentLedgerReader) -> bool:
    events = ledger.lifecycle_events(strategy_version)
    return bool(events) and events[-1].event.to_state is StrategyLifecycleState.REJECTED


def _canonical_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip().casefold())


__all__ = (
    "CritiqueReport",
    "DeterministicHypothesisCritic",
    "HypothesisCritic",
    "Objection",
    "ObjectionKind",
    "Severity",
)
