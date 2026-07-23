from __future__ import annotations

from dataclasses import dataclass
from typing import override

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_research_loop_models import IntradayHypothesisSelection
from trading_agent.lane_contract_models import ExperimentScope


class InvalidIntradayTrialDesignError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday historical trial design is invalid"


@dataclass(frozen=True, slots=True)
class ResolvedIntradayTrialDesign:
    strategy_version: str
    experiment_scope: ExperimentScope


def resolve_intraday_trial_design(
    selection: IntradayHypothesisSelection,
    code_version: str,
    ledger: ExperimentLedgerReader,
) -> ResolvedIntradayTrialDesign:
    if not selection.is_source_backed:
        contract = strategy_contract(selection.strategy)
        if selection.hypothesis_id != contract.hypothesis_id:
            raise InvalidIntradayTrialDesignError
        return ResolvedIntradayTrialDesign(
            strategy_version=strategy_version_identity(selection.strategy, code_version),
            experiment_scope=contract.experiment_scope,
        )

    strategy_version = selection.strategy_version
    queue_card_key = selection.queue_card_key
    if strategy_version is None or queue_card_key is None:
        raise InvalidIntradayTrialDesignError
    versions = tuple(
        stored.registration
        for stored in ledger.strategy_versions()
        if stored.registration.strategy_version == strategy_version
    )
    cards = tuple(
        stored.card for stored in ledger.research_hypothesis_cards() if str(stored.card_key) == queue_card_key
    )
    if len(versions) != 1 or len(cards) != 1:
        raise InvalidIntradayTrialDesignError
    version = versions[0]
    card = cards[0]
    hypothesis = card.hypothesis
    template = strategy_contract(selection.strategy)
    if (
        hypothesis.hypothesis_id != selection.hypothesis_id
        or version.strategy_id != selection.strategy.value
        or version.hypothesis_id != hypothesis.hypothesis_id
        or version.experiment_scope_key != hypothesis.experiment_scope_key
        or version.lane_id is not hypothesis.primary_lane
        or version.code_version != code_version
        or version.parameter_set != template.parameter_set
        or version.data_contract != CURRENT_DATA_CONTRACT
        or version.cost_model != CURRENT_COST_MODEL
        or version.portfolio_policy != SHADOW_PORTFOLIO_POLICY
        or version.source_registered_at != hypothesis.source_registered_at
    ):
        raise InvalidIntradayTrialDesignError
    return ResolvedIntradayTrialDesign(
        strategy_version=version.strategy_version,
        experiment_scope=hypothesis.experiment_scope,
    )


__all__ = (
    "InvalidIntradayTrialDesignError",
    "ResolvedIntradayTrialDesign",
    "resolve_intraday_trial_design",
)
