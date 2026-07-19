from __future__ import annotations

import hashlib
from typing import NewType

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent

MultiMarketLifecycleEventKey = NewType("MultiMarketLifecycleEventKey", str)


def multi_market_lifecycle_event_key(
    event: MultiMarketStrategyLifecycleEvent,
) -> MultiMarketLifecycleEventKey:
    return MultiMarketLifecycleEventKey(hashlib.sha256(canonical_experiment_ledger_json(event).encode()).hexdigest())
