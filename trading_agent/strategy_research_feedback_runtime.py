from __future__ import annotations

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_policy import (
    FeedbackWorkAdmission,
    OwnerFeedbackDecision,
    OwnerFeedbackRouter,
    admit_feedback_work,
)
from trading_agent.strategy_research_runtime_models import StrategyResearchWork
from trading_agent.strategy_research_types import ResearchAgentId


def owner_feedback(
    store: ExperimentLedgerStore,
    owner_agent_id: ResearchAgentId,
) -> OwnerFeedbackDecision | None:
    feedback = ExperimentLedgerReader(store.path).strategy_research_feedback(owner_agent_id)
    return OwnerFeedbackRouter(feedback).for_owner(owner_agent_id)


def feedback_admission(
    store: ExperimentLedgerStore,
    agent_id: ResearchAgentId,
    work: StrategyResearchWork | None,
) -> FeedbackWorkAdmission | None:
    if work is None:
        return None
    reader = ExperimentLedgerReader(store.path)
    decision = OwnerFeedbackRouter(reader.strategy_research_feedback(agent_id)).for_owner(agent_id)
    if decision is None:
        return None
    manifests = tuple(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == decision.hypothesis_id
    )
    if len(manifests) != 1:
        raise StrategyResearchLedgerError("feedback_prior_hypothesis_missing")
    return admit_feedback_work(decision, manifests[0].hypothesis, work)


__all__ = ("feedback_admission", "owner_feedback")
