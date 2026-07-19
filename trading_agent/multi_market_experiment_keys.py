from __future__ import annotations

import hashlib
from typing import NewType

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.multi_market_experiment_models import (
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
)

MultiMarketHypothesisRegistrationKey = NewType(
    "MultiMarketHypothesisRegistrationKey",
    str,
)
MultiMarketStrategyVersionRegistrationKey = NewType(
    "MultiMarketStrategyVersionRegistrationKey",
    str,
)


def multi_market_hypothesis_registration_key(
    registration: MultiMarketHypothesisRegistration,
) -> MultiMarketHypothesisRegistrationKey:
    return MultiMarketHypothesisRegistrationKey(
        hashlib.sha256(canonical_experiment_ledger_json(registration).encode()).hexdigest()
    )


def multi_market_strategy_version_registration_key(
    registration: MultiMarketStrategyVersionRegistration,
) -> MultiMarketStrategyVersionRegistrationKey:
    return MultiMarketStrategyVersionRegistrationKey(
        hashlib.sha256(canonical_experiment_ledger_json(registration).encode()).hexdigest()
    )
