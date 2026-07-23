from __future__ import annotations

import datetime as dt

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialRegistration,
    ResearchHypothesisCard,
    ResearchSourceKind,
    StrategyVersionRegistration,
    TrialEventKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_research_loop_models import (
    IntradayHypothesisSelection,
    IntradayResearchManifest,
)
from trading_agent.lane_identity_models import LaneId
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
    SourceDrivenHypothesisQueueItem,
)

_REFRESH_ROUTES = frozenset(
    {
        HypothesisQueueRoute.HISTORICAL_REPLAY,
        HypothesisQueueRoute.INDEPENDENT_REVIEW,
        HypothesisQueueRoute.RECOVERY,
    }
)


def validated_source_backed_registration(
    reader: ExperimentLedgerReader,
    item: SourceDrivenHypothesisQueueItem,
    selection: IntradayHypothesisSelection,
    card: ResearchHypothesisCard,
    source_kinds: tuple[ResearchSourceKind, ...],
    manifest: IntradayResearchManifest,
    *,
    queue_is_current: bool,
) -> StrategyVersionRegistration:
    strategy_version = selection.strategy_version
    if strategy_version is None or not _queue_item_matches(item, selection, card, source_kinds):
        raise ValueError
    prior = tuple(
        stored.registration
        for stored in reader.strategy_versions()
        if stored.registration.hypothesis_id == selection.hypothesis_id
    )
    completed_trial = _exact_completed_trial(
        reader,
        strategy_version,
        manifest.input_sha256,
    )
    exact_trial_replay = (
        completed_trial is not None
        and completed_trial.registered_at
        == manifest.registered_at + dt.timedelta(seconds=1)
    )
    registration = _registration(
        selection,
        strategy_version,
        card,
        manifest,
        prior[0].ledger_recorded_at if prior else manifest.registered_at,
    )
    exact_registration_replay = (
        len(prior) == 1
        and prior[0] == registration
        and manifest.registered_at == prior[0].ledger_recorded_at
    )
    if completed_trial is not None and not exact_trial_replay:
        raise ValueError
    if (
        not queue_is_current
        and not exact_trial_replay
        and not exact_registration_replay
    ) or not _queue_state_accepts_version(
        item,
        prior,
        strategy_version,
        exact_trial_replay=exact_trial_replay,
        exact_registration_replay=exact_registration_replay,
    ):
        raise ValueError
    if prior and prior[0] != registration:
        raise ValueError
    return registration


def _queue_item_matches(
    item: SourceDrivenHypothesisQueueItem,
    selection: IntradayHypothesisSelection,
    card: ResearchHypothesisCard,
    source_kinds: tuple[ResearchSourceKind, ...],
) -> bool:
    hypothesis = card.hypothesis
    return (
        item.hypothesis_id == selection.hypothesis_id
        and item.lane_id is LaneId.INTRADAY_MOMENTUM
        and item.registered_at == hypothesis.ledger_recorded_at
        and item.hypothesis == hypothesis.hypothesis
        and item.falsification_rule == hypothesis.falsification_rule
        and item.economic_mechanism == card.economic_mechanism
        and item.counterfactual_baseline == card.counterfactual_baseline
        and item.source_keys == card.research_source_keys
        and item.source_kinds == source_kinds
        and hypothesis.hypothesis_id == selection.hypothesis_id
        and hypothesis.primary_lane is LaneId.INTRADAY_MOMENTUM
    )


def _queue_state_accepts_version(
    item: SourceDrivenHypothesisQueueItem,
    prior: tuple[StrategyVersionRegistration, ...],
    strategy_version: str,
    *,
    exact_trial_replay: bool,
    exact_registration_replay: bool,
) -> bool:
    if not prior:
        return (
            item.route is HypothesisQueueRoute.STRATEGY_DESIGN
            and not item.strategy_versions
            and not item.historical_trial_ids
        )
    if exact_trial_replay:
        return len(prior) == 1 and item.strategy_versions in ((), (strategy_version,))
    if exact_registration_replay:
        return item.route is HypothesisQueueRoute.STRATEGY_DESIGN and not item.strategy_versions
    return (
        len(prior) == 1
        and item.route in _REFRESH_ROUTES
        and item.strategy_versions == (strategy_version,)
        and prior[0].strategy_version == strategy_version
    )


def _registration(
    selection: IntradayHypothesisSelection,
    strategy_version: str,
    card: ResearchHypothesisCard,
    manifest: IntradayResearchManifest,
    ledger_recorded_at: dt.datetime,
) -> StrategyVersionRegistration:
    template = strategy_contract(selection.strategy)
    return StrategyVersionRegistration(
        strategy_id=selection.strategy.value,
        strategy_version=strategy_version,
        hypothesis_id=card.hypothesis.hypothesis_id,
        experiment_scope_key=card.hypothesis.experiment_scope_key,
        lane_id=card.hypothesis.primary_lane,
        code_version=manifest.code_version,
        parameter_set=template.parameter_set,
        data_contract=CURRENT_DATA_CONTRACT,
        cost_model=CURRENT_COST_MODEL,
        portfolio_policy=SHADOW_PORTFOLIO_POLICY,
        source_registered_at=card.hypothesis.source_registered_at,
        ledger_recorded_at=ledger_recorded_at,
    )


def _exact_completed_trial(
    reader: ExperimentLedgerReader,
    strategy_version: str,
    data_version: str | None,
) -> ExperimentTrialRegistration | None:
    if data_version is None:
        return None
    matching = tuple(
        stored.registration
        for stored in reader.trials()
        if stored.registration.strategy_version == strategy_version
        and stored.registration.data_version == data_version
    )
    if len(matching) != 1:
        return None
    events = reader.trial_events(matching[0].trial_id)
    if not events or events[-1].event.event_kind is not TrialEventKind.COMPLETED:
        return None
    return matching[0]


__all__ = ("validated_source_backed_registration",)
