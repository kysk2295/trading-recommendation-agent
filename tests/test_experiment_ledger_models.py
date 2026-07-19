from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
    experiment_trial_event_key,
    experiment_trial_registration_key,
    hypothesis_registration_key,
    research_hypothesis_card_key,
    research_source_key,
    strategy_lifecycle_event_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    ResearchSourceKind,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialEventKind,
    TrialKind,
    lifecycle_state_rank,
    lifecycle_transition_allowed,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_defaults import current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.strategy_factory import StrategyMode

ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
SOURCE_REGISTERED_AT = ORB_SCOPE.registered_at
LEDGER_RECORDED_AT = dt.datetime(2026, 7, 15, 12, tzinfo=dt.UTC)
DECIDED_AT = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)
DECISION_DATE = dt.date(2026, 7, 15)
EFFECTIVE_DATE = dt.date(2026, 7, 16)


def _hypothesis() -> HypothesisRegistration:
    return HypothesisRegistration(
        hypothesis_id=ORB_CONTRACT.hypothesis_id,
        experiment_scope=ORB_SCOPE,
        experiment_scope_key=experiment_scope_key(ORB_SCOPE),
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        hypothesis=ORB_CONTRACT.hypothesis,
        falsification_rule=ORB_CONTRACT.falsification_rule,
        source_registered_at=SOURCE_REGISTERED_AT,
        ledger_recorded_at=LEDGER_RECORDED_AT,
    )


def _version() -> StrategyVersionRegistration:
    return StrategyVersionRegistration(
        strategy_id=StrategyMode.ORB.value,
        strategy_version=ORB_CONTRACT.strategy_version,
        hypothesis_id=ORB_CONTRACT.hypothesis_id,
        experiment_scope_key=experiment_scope_key(ORB_SCOPE),
        lane_id=LaneId.INTRADAY_MOMENTUM,
        code_version="a" * 40,
        parameter_set=ORB_CONTRACT.parameter_set,
        data_contract=CURRENT_DATA_CONTRACT,
        cost_model=CURRENT_COST_MODEL,
        portfolio_policy=SHADOW_PORTFOLIO_POLICY,
        source_registered_at=SOURCE_REGISTERED_AT,
        ledger_recorded_at=LEDGER_RECORDED_AT,
    )


def _trial(
    *,
    scope: ExperimentScope = ORB_SCOPE,
    trial_kind: TrialKind = TrialKind.SHADOW_FORWARD,
) -> ExperimentTrialRegistration:
    return ExperimentTrialRegistration(
        trial_id="trial-orb-shadow-20260716",
        strategy_version=ORB_CONTRACT.strategy_version,
        trial_kind=trial_kind,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        evaluator_version="paper_metrics_day_block_bootstrap_v2",
        data_version="b" * 64,
        feed_entitlement="KIS_read_only_rankings",
        planned_start=dt.date(2026, 7, 16),
        planned_end=dt.date(2026, 7, 17),
        registered_at=LEDGER_RECORDED_AT,
        evidence_budget=(
            "minimum_completed_trades:100",
            "minimum_forward_sessions:60",
        ),
    )


def _started_event() -> ExperimentTrialEvent:
    return ExperimentTrialEvent(
        trial_id="trial-orb-shadow-20260716",
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 16, 13, 20, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )


def _completed_event(started: ExperimentTrialEvent) -> ExperimentTrialEvent:
    return ExperimentTrialEvent(
        trial_id=started.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=dt.datetime(2026, 7, 16, 21, tzinfo=dt.UTC),
        artifact_sha256s=("c" * 64,),
        reason_codes=(),
        previous_event_key=experiment_trial_event_key(started),
    )


def _lifecycle_registration() -> StrategyLifecycleEvent:
    evidence_keys = tuple(
        sorted(
            (
                str(experiment_scope_key(ORB_SCOPE)),
                str(hypothesis_registration_key(_hypothesis())),
                str(strategy_version_registration_key(_version())),
            )
        )
    )
    return StrategyLifecycleEvent(
        strategy_version=ORB_CONTRACT.strategy_version,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="strategy_lifecycle_v1",
        decision_session_date=DECISION_DATE,
        effective_session_date=EFFECTIVE_DATE,
        decided_at=DECIDED_AT,
        evidence_keys=evidence_keys,
        reason_codes=("existing_contract_import",),
        previous_event_key=None,
    )


def _research_source() -> ResearchSource:
    return ResearchSource(
        source_id="academic-momentum-1993",
        source_kind=ResearchSourceKind.ACADEMIC_PAPER,
        title="Returns to Buying Winners and Selling Losers",
        source_url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
        published_on=dt.date(1993, 2, 1),
        claim="Intermediate-horizon relative strength motivates a momentum trial.",
        limitations="It is not current-market or net-cost evidence for this project.",
        retrieved_at=LEDGER_RECORDED_AT,
        ledger_recorded_at=LEDGER_RECORDED_AT,
    )


def test_research_source_and_hypothesis_card_have_canonical_immutable_keys() -> None:
    source = _research_source()
    card = ResearchHypothesisCard(
        hypothesis=_hypothesis(),
        research_source_keys=(str(research_source_key(source)),),
        economic_mechanism="Underreaction may leave return continuation.",
        counterfactual_baseline="Matched eligible-universe forward return after the same cost model.",
    )

    assert len(research_source_key(source)) == 64
    assert len(research_hypothesis_card_key(card)) == 64
    assert card.research_source_keys == (str(research_source_key(source)),)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_url", "http://doi.org/10.1111/j.1540-6261.1993.tb04702.x"),
        ("source_url", "https://user:password@doi.org/paper"),
        ("source_url", "https://doi.org/paper#fragment"),
        ("source_url", "https://doi.org/paper?access_token=secret"),
        ("retrieved_at", dt.datetime(2026, 7, 15, 12)),
    ),
)
def test_research_source_rejects_noncanonical_locator_or_time(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _ = ResearchSource.model_validate(_research_source().model_dump(mode="python") | {field: value})


def test_canonical_models_and_keys_are_stable() -> None:
    hypothesis = _hypothesis()
    version = _version()
    trial = _trial()
    started = _started_event()
    completed = _completed_event(started)
    lifecycle = _lifecycle_registration()

    keys = (
        hypothesis_registration_key(hypothesis),
        strategy_version_registration_key(version),
        experiment_trial_registration_key(trial),
        experiment_trial_event_key(started),
        experiment_trial_event_key(completed),
        strategy_lifecycle_event_key(lifecycle),
    )

    assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") for key in keys)
    assert canonical_experiment_ledger_json(hypothesis) == canonical_experiment_ledger_json(
        HypothesisRegistration.model_validate_json(hypothesis.model_dump_json())
    )


def test_hypothesis_rejects_scope_identity_and_time_mismatch() -> None:
    valid = _hypothesis().model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = HypothesisRegistration.model_validate(valid | {"experiment_scope_key": "0" * 64})
    with pytest.raises(ValidationError):
        _ = HypothesisRegistration.model_validate(valid | {"primary_lane": LaneId.SWING_MOMENTUM})
    with pytest.raises(ValidationError):
        _ = HypothesisRegistration.model_validate(
            valid | {"ledger_recorded_at": SOURCE_REGISTERED_AT - dt.timedelta(microseconds=1)}
        )
    with pytest.raises(ValidationError):
        _ = HypothesisRegistration.model_validate(valid | {"ledger_recorded_at": dt.datetime(2026, 7, 15)})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("strategy_version", "bad version"),
        ("parameter_set", ("duplicate", "duplicate")),
        ("data_contract", ("",)),
        ("cost_model", (" padded ",)),
        ("portfolio_policy", ()),
    ),
)
def test_strategy_version_rejects_invalid_identity_or_contract(
    field: str,
    value: object,
) -> None:
    data = _version().model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = StrategyVersionRegistration.model_validate(data | {field: value})


def test_trial_requires_preregistration_and_matching_scope_kind() -> None:
    valid = _trial().model_dump(mode="python")
    after_open = dt.datetime(2026, 7, 16, 14, tzinfo=dt.UTC)
    cross_scope = ExperimentScope(
        scope_kind=ExperimentScopeKind.CROSS_LANE_HYPOTHESIS,
        hypothesis_id="H-CROSS-001",
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        lanes=(LaneId.INTRADAY_MOMENTUM, LaneId.MARKET_REGIME),
        source_hypothesis_ids=("H-MOM-ORB-001", "H-REGIME-001"),
        combination_rule="preopen VIX gate applied to all ORB candidates",
        registered_at=SOURCE_REGISTERED_AT,
    )

    with pytest.raises(ValidationError):
        _ = ExperimentTrialRegistration.model_validate(valid | {"registered_at": after_open})
    with pytest.raises(ValidationError):
        _ = ExperimentTrialRegistration.model_validate(valid | {"planned_end": dt.date(2026, 7, 15)})
    with pytest.raises(ValidationError):
        _ = ExperimentTrialRegistration.model_validate(valid | {"trial_kind": TrialKind.CROSS_LANE_HYPOTHESIS})
    with pytest.raises(ValidationError):
        _ = ExperimentTrialRegistration.model_validate(
            _trial(scope=cross_scope, trial_kind=TrialKind.SHADOW_FORWARD).model_dump(mode="python")
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"sequence": 0},
        {"artifact_sha256s": ("d" * 64,)},
        {"reason_codes": ("unexpected_reason",)},
        {"event_kind": TrialEventKind.COMPLETED},
    ),
)
def test_started_trial_event_has_exact_shape(changes: dict[str, object]) -> None:
    data = _started_event().model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = ExperimentTrialEvent.model_validate(data | changes)


@pytest.mark.parametrize(
    "changes",
    (
        {"artifact_sha256s": ()},
        {"previous_event_key": None},
        {"reason_codes": ("reason_b", "reason_a")},
    ),
)
def test_completed_trial_event_requires_artifact_and_chain(
    changes: dict[str, object],
) -> None:
    data = _completed_event(_started_event()).model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = ExperimentTrialEvent.model_validate(data | changes)


@pytest.mark.parametrize("event_kind", (TrialEventKind.FAILED, TrialEventKind.CENSORED))
def test_failed_or_censored_trial_event_requires_reason(event_kind: TrialEventKind) -> None:
    data = _completed_event(_started_event()).model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = ExperimentTrialEvent.model_validate(
            data | {"event_kind": event_kind, "artifact_sha256s": (), "reason_codes": ()}
        )


def test_lifecycle_registration_rejects_unproven_or_forbidden_initial_state() -> None:
    valid = _lifecycle_registration().model_dump(mode="python")

    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(valid | {"reason_codes": ("other",)})
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(valid | {"to_state": StrategyLifecycleState.PAPER_CHAMPION})
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(valid | {"from_state": StrategyLifecycleState.HISTORICAL})
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(valid | {"previous_event_key": "f" * 64})


def test_lifecycle_transition_uses_closed_state_table_and_next_session() -> None:
    registration = _lifecycle_registration()
    transition = StrategyLifecycleEvent(
        strategy_version=registration.strategy_version,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        policy_version=registration.policy_version,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
        evidence_keys=("f" * 64,),
        reason_codes=("paper_smoke_verified",),
        previous_event_key=strategy_lifecycle_event_key(registration),
    )

    assert transition.from_state is not None
    assert lifecycle_transition_allowed(transition.from_state, transition.to_state) is True
    assert (
        lifecycle_transition_allowed(
            StrategyLifecycleState.IDEA,
            StrategyLifecycleState.PAPER_CHAMPION,
        )
        is False
    )

    data = transition.model_dump(mode="python")
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(data | {"to_state": StrategyLifecycleState.PAPER_CHAMPION})
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(data | {"effective_session_date": EFFECTIVE_DATE})
    with pytest.raises(ValidationError):
        _ = StrategyLifecycleEvent.model_validate(data | {"effective_session_date": dt.date(2026, 7, 18)})


def test_lifecycle_transition_has_distinct_shadow_champion_path() -> None:
    assert (
        lifecycle_transition_allowed(
            StrategyLifecycleState.CHALLENGER,
            StrategyLifecycleState.SHADOW_CHAMPION,
        )
        is True
    )
    assert (
        lifecycle_transition_allowed(
            StrategyLifecycleState.EXPERIMENTAL_PAPER,
            StrategyLifecycleState.SHADOW_CHAMPION,
        )
        is False
    )
    assert lifecycle_state_rank(StrategyLifecycleState.SHADOW_CHAMPION) == lifecycle_state_rank(
        StrategyLifecycleState.PAPER_CHAMPION
    )
