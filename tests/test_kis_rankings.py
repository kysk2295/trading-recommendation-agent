from __future__ import annotations

from trading_agent import kis_rankings


def test_daytime_rankings_use_blue_ocean_exchange_codes() -> None:
    # Given/When: the dedicated KIS daytime exchange universe is inspected.
    exchanges = kis_rankings.DAYTIME_EXCHANGES

    # Then: it stays distinct from regular and premarket venue codes.
    assert exchanges == ("BAQ", "BAY", "BAA")
    assert set(exchanges).isdisjoint(kis_rankings.US_EXCHANGES)
