from __future__ import annotations

import hashlib
import json
from typing import NewType

from pydantic import BaseModel

from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    StrategyLifecycleEvent,
    StrategyVersionRegistration,
)

HypothesisRegistrationKey = NewType("HypothesisRegistrationKey", str)
StrategyVersionRegistrationKey = NewType("StrategyVersionRegistrationKey", str)
ExperimentTrialRegistrationKey = NewType("ExperimentTrialRegistrationKey", str)
ExperimentTrialEventKey = NewType("ExperimentTrialEventKey", str)
StrategyLifecycleEventKey = NewType("StrategyLifecycleEventKey", str)


def hypothesis_registration_key(registration: HypothesisRegistration) -> HypothesisRegistrationKey:
    return HypothesisRegistrationKey(_model_sha256(registration))


def strategy_version_registration_key(
    registration: StrategyVersionRegistration,
) -> StrategyVersionRegistrationKey:
    return StrategyVersionRegistrationKey(_model_sha256(registration))


def experiment_trial_registration_key(
    registration: ExperimentTrialRegistration,
) -> ExperimentTrialRegistrationKey:
    return ExperimentTrialRegistrationKey(_model_sha256(registration))


def experiment_trial_event_key(event: ExperimentTrialEvent) -> ExperimentTrialEventKey:
    return ExperimentTrialEventKey(_model_sha256(event))


def strategy_lifecycle_event_key(event: StrategyLifecycleEvent) -> StrategyLifecycleEventKey:
    return StrategyLifecycleEventKey(_model_sha256(event))


def canonical_experiment_ledger_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(model).encode()).hexdigest()
