from __future__ import annotations

import hashlib
from typing import NewType

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ExperimentTrialEvent
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration

MultiMarketTrialRegistrationKey = NewType("MultiMarketTrialRegistrationKey", str)
MultiMarketTrialEventKey = NewType("MultiMarketTrialEventKey", str)


def multi_market_trial_registration_key(
    registration: MultiMarketExperimentTrialRegistration,
) -> MultiMarketTrialRegistrationKey:
    return MultiMarketTrialRegistrationKey(_content_key(registration))


def multi_market_trial_event_key(event: ExperimentTrialEvent) -> MultiMarketTrialEventKey:
    return MultiMarketTrialEventKey(_content_key(event))


def _content_key(value: MultiMarketExperimentTrialRegistration | ExperimentTrialEvent) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(value).encode()).hexdigest()
