from __future__ import annotations

from trading_agent.kis_provider import KisRankedStock, select_ranked_stocks


def test_select_ranked_stocks_deduplicates_and_keeps_momentum() -> None:
    fast_rank_one = _stock("FAST", 0.08, 1_000_000, 1)
    fast_with_average = _stock("FAST", 0.08, 200_000, 2)
    slow = _stock("SLOW", 0.02, 200_000, 1)

    selected = select_ranked_stocks(((fast_rank_one,), (fast_with_average, slow)), limit=5)

    assert tuple(stock.symbol for stock in selected) == ("FAST",)
    assert selected[0].average_daily_volume == 200_000


def _stock(symbol: str, change_pct: float, average_daily_volume: int, rank: int) -> KisRankedStock:
    return KisRankedStock(
        "NAS",
        symbol,
        symbol.title(),
        10.0,
        change_pct,
        9.99,
        10.01,
        1_000_000,
        10_000_000.0,
        average_daily_volume,
        rank,
    )
