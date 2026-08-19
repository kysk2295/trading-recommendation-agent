from __future__ import annotations

import hashlib
import json
from typing import NewType

from pydantic import BaseModel

from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    StrategyLifecycleEvent,
    StrategyVersionRegistration,
)
from trading_agent.strategy_authority_models import StrategyAuthorityBinding

HypothesisRegistrationKey = NewType("HypothesisRegistrationKey", str)
StrategyVersionRegistrationKey = NewType("StrategyVersionRegistrationKey", str)
ExperimentTrialRegistrationKey = NewType("ExperimentTrialRegistrationKey", str)
ExperimentTrialEventKey = NewType("ExperimentTrialEventKey", str)
StrategyLifecycleEventKey = NewType("StrategyLifecycleEventKey", str)
ResearchSourceKey = NewType("ResearchSourceKey", str)
ResearchHypothesisCardKey = NewType("ResearchHypothesisCardKey", str)
StrategyAuthorityBindingKey = NewType("StrategyAuthorityBindingKey", str)
DayHypothesisFamilyKey = NewType("DayHypothesisFamilyKey", str)
DayHypothesisVersionKey = NewType("DayHypothesisVersionKey", str)


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


def research_source_key(source: ResearchSource) -> ResearchSourceKey:
    return ResearchSourceKey(_model_sha256(source))


def research_hypothesis_card_key(card: ResearchHypothesisCard) -> ResearchHypothesisCardKey:
    return ResearchHypothesisCardKey(_model_sha256(card))


def strategy_authority_binding_key(
    binding: StrategyAuthorityBinding,
) -> StrategyAuthorityBindingKey:
    return StrategyAuthorityBindingKey(_model_sha256(binding))


def day_hypothesis_family_key(family: HypothesisFamily) -> DayHypothesisFamilyKey:
    return DayHypothesisFamilyKey(_model_sha256(HypothesisFamily.model_validate(family)))


def day_hypothesis_version_key(version: HypothesisVersion) -> DayHypothesisVersionKey:
    return DayHypothesisVersionKey(_model_sha256(HypothesisVersion.model_validate(version)))


def canonical_experiment_ledger_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(model).encode()).hexdigest()
