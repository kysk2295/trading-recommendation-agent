from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable

from trading_agent.alpaca_sip_live_actionability import AlpacaSipLiveActionabilityDependencies
from trading_agent.alpaca_sip_trade_stream import connect_alpaca_sip_trade_stream


def default_alpaca_sip_live_actionability_dependencies(
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> AlpacaSipLiveActionabilityDependencies:
    return AlpacaSipLiveActionabilityDependencies(
        connect_alpaca_sip_trade_stream,
        clock,
        lambda: uuid.uuid4().hex,
        lambda event, seconds: event.wait(seconds),
    )


__all__ = ("default_alpaca_sip_live_actionability_dependencies",)
