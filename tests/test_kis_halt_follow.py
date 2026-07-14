from __future__ import annotations

import run_kis_paper_scan
from trading_agent.kis_provider import KisRankedStock


def test_active_halt_is_removed_from_the_tracked_follow_path() -> None:
    halted = _tracked_stock("HALTED")
    tradable = _tracked_stock("TRADABLE")

    allowed, blocked = run_kis_paper_scan.partition_halted_candidates(
        (halted, tradable),
        frozenset({"HALTED"}),
    )

    assert tuple(stock.symbol for stock in allowed) == ("TRADABLE",)
    assert tuple(stock.symbol for stock in blocked) == ("HALTED",)


def _tracked_stock(symbol: str) -> KisRankedStock:
    return KisRankedStock(
        "NAS",
        symbol,
        symbol.title(),
        10.0,
        0.1,
        9.99,
        10.01,
        500_000,
        5_000_000.0,
        200_000,
        1,
    )
