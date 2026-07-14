from __future__ import annotations

from typing import Protocol

from trading_agent.models import BarInput, MomentumCandidate, StrategySignal


class IntradayStrategy(Protocol):
    name: str

    def observe(
        self,
        bar: BarInput,
        candidate: MomentumCandidate | None,
    ) -> StrategySignal | None: ...
