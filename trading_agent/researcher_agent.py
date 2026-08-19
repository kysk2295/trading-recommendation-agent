from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from trading_agent.experiment_ledger_models import ResearchHypothesisCard, ResearchSource
from trading_agent.lane_identity_models import LaneId


@dataclass(frozen=True, slots=True)
class FailureDigest:
    censored_reasons: tuple[str, ...]
    failed_falsifications: tuple[str, ...]
    rejected_hypothesis_texts: tuple[str, ...]
    reviewer_decisions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearcherContext:
    lane_id: LaneId
    sources: tuple[ResearchSource, ...]
    failure_digest: FailureDigest
    regime_context: str
    existing_hypothesis_texts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LlmCallReceipt:
    model_id: str
    prompt_sha256: str
    response_sha256: str
    seed: int | None
    temperature: float
    called_at: dt.datetime


@dataclass(frozen=True, slots=True)
class CandidateStrategyDraft:
    source_code: str
    free_parameters: tuple[str, ...]
    methodology_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProposedHypothesis:
    card: ResearchHypothesisCard
    cited_sources: tuple[ResearchSource, ...]
    llm_receipt: LlmCallReceipt
    strategy_draft: CandidateStrategyDraft


class HypothesisGenerator(Protocol):
    def propose(self, context: ResearcherContext) -> ProposedHypothesis: ...


@dataclass(frozen=True, slots=True)
class FixedHypothesisGenerator:
    proposal: ProposedHypothesis

    def propose(self, context: ResearcherContext) -> ProposedHypothesis:
        del context
        return self.proposal


__all__ = (
    "CandidateStrategyDraft",
    "FailureDigest",
    "FixedHypothesisGenerator",
    "HypothesisGenerator",
    "LlmCallReceipt",
    "ProposedHypothesis",
    "ResearcherContext",
)
