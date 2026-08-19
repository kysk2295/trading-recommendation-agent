from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.strategy_research_experiment_models import ScienceCycleResult
from trading_agent.strategy_research_holdout_reviewer import SealedHoldoutReviewer
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_observation_builders import (
    MethodologyObservationInput,
    build_methodology_observation,
)
from trading_agent.strategy_research_policy import MethodologyPolicyError
from trading_agent.strategy_research_runtime_models import (
    InvalidStrategyResearchWorkSourceError,
    StrategyResearchCycleRunner,
    StrategyResearchWork,
)
from trading_agent.strategy_research_science_kernel import ScienceKernel
from trading_agent.strategy_research_types import CanonicalModel, ResearchAgentId


class StrategyResearchWorkQueue(CanonicalModel):
    items: tuple[StrategyResearchWork, ...] = Field(min_length=1)


class PrivateStrategyResearchWorkSource:
    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().absolute()

    def next_work(
        self,
        agent_id: ResearchAgentId,
        evidence_cursor: str | None,
    ) -> StrategyResearchWork | None:
        path = self._root / f"{agent_id.value}.json"
        if not path.exists():
            return None
        try:
            queue = StrategyResearchWorkQueue.model_validate_json(read_private_text(path))
        except (InvalidPrivateImmutableFileError, ValidationError):
            raise InvalidStrategyResearchWorkSourceError("private_work_queue_invalid") from None
        ordered = tuple(sorted(queue.items, key=lambda item: (item.available_at, item.evidence_event_id)))
        if any(item.draft.agent_id is not agent_id for item in ordered):
            raise InvalidStrategyResearchWorkSourceError("private_work_owner_mismatch")
        event_ids = tuple(item.evidence_event_id for item in ordered)
        if len(event_ids) != len(set(event_ids)):
            raise InvalidStrategyResearchWorkSourceError("private_work_event_duplicate")
        for item in ordered:
            self._admit_methodology_sources(item)
        if evidence_cursor is None:
            return ordered[0]
        if evidence_cursor not in event_ids:
            raise InvalidStrategyResearchWorkSourceError("private_work_cursor_missing")
        next_index = event_ids.index(evidence_cursor) + 1
        if next_index == len(ordered):
            return None
        return ordered[next_index]

    @staticmethod
    def _admit_methodology_sources(work: StrategyResearchWork) -> None:
        try:
            observation = build_methodology_observation(
                MethodologyObservationInput(work.draft.agent_id, work.available_at, work.source_receipts)
            )
        except MethodologyPolicyError as error:
            raise InvalidStrategyResearchWorkSourceError(error.reason) from None
        evidence_source_ids = {item.source_id for item in work.draft.source_refs}
        if not set(observation.source_ids).issubset(evidence_source_ids):
            raise InvalidStrategyResearchWorkSourceError("private_work_evidence_ref_mismatch")
        if not observation.ready:
            raise InvalidStrategyResearchWorkSourceError(observation.waiting_reason or "methodology_source_waiting")


class ScienceKernelCycleRunner(StrategyResearchCycleRunner):
    __slots__ = ("_store",)

    def __init__(self, store: ExperimentLedgerStore) -> None:
        self._store = store

    def run(self, work: StrategyResearchWork) -> ScienceCycleResult:
        if work.sealed_holdout is None or work.experiment is None:
            raise StrategyResearchLedgerError("outcome_evidence_missing")
        reviewer = SealedHoldoutReviewer.from_payload(work.sealed_holdout)
        return ScienceKernel(self._store, reviewer).run(work.draft, work.experiment)


__all__ = (
    "PrivateStrategyResearchWorkSource",
    "ScienceKernelCycleRunner",
    "StrategyResearchWorkQueue",
)
