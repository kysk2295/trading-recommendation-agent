from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import httpx2
import pytest

from trading_agent.kis_provider import KisRankedStock
from trading_agent.market_risk import (
    MARKET_RISK_HEADER,
    HaltFeedFormatError,
    HaltSnapshot,
    MarketRiskConfig,
    MarketRiskGate,
    RiskRejectReason,
    fetch_active_halts,
    write_market_risk_screen,
)


def test_official_halt_feed_blocks_halted_stock_before_portfolio_selection() -> None:
    payload = (
        "Halt Date,Halt Time,Symbol,Name,Exchange,Reason,Resume Date,NYSE Resume Time\n"
        "2026-07-10,19:50:00,HALT,Halted Inc,Nasdaq,News Pending,,\n"
    )
    with httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, text=payload, request=request)
        )
    ) as client:
        observed_at = dt.datetime(2026, 7, 13, 5, 0, tzinfo=dt.UTC)
        halts = fetch_active_halts(client, lambda: observed_at)

    assert halts.observed_at == observed_at
    gate = MarketRiskGate(halts, MarketRiskConfig())
    screen = gate.screen(((_stock("HALT", 0.20), _stock("SAFE", 0.10)),), 1)

    assert tuple(stock.symbol for stock in screen.selected) == ("SAFE",)
    assert screen.rejected[0].stock.symbol == "HALT"
    assert screen.rejected[0].reason is RiskRejectReason.ACTIVE_HALT


def test_market_risk_gate_rejects_missing_crossed_and_wide_quotes() -> None:
    gate = MarketRiskGate(_empty_halts(), MarketRiskConfig())
    screen = gate.screen(
        (
            (
                _stock("MISSING", 0.20, quote=(0.0, 0.0)),
                _stock("CROSSED", 0.19, quote=(10.10, 10.00)),
                _stock("WIDE", 0.18, quote=(9.90, 10.10)),
                _stock("SAFE", 0.17),
            ),
        ),
        1,
    )

    assert tuple(stock.symbol for stock in screen.selected) == ("SAFE",)
    assert tuple(row.reason for row in screen.rejected) == (
        RiskRejectReason.MISSING_QUOTE,
        RiskRejectReason.CROSSED_QUOTE,
        RiskRejectReason.WIDE_SPREAD,
    )


def test_market_risk_gate_reserves_slippage_in_all_in_cost() -> None:
    config = MarketRiskConfig(
        max_spread_bps=200.0,
        slippage_per_side_bps=20.0,
        max_round_trip_cost_bps=100.0,
    )
    gate = MarketRiskGate(_empty_halts(), config)

    screen = gate.screen(((_stock("COSTLY", 0.10, quote=(9.96, 10.04)),),), 1)

    assert screen.selected == ()
    assert screen.rejected[0].reason is RiskRejectReason.ESTIMATED_COST
    assert screen.rejected[0].estimated_round_trip_cost_bps == pytest.approx(120.0)


def test_market_risk_gate_preserves_eligible_candidates_after_portfolio_limit(tmp_path: Path) -> None:
    gate = MarketRiskGate(_empty_halts(), MarketRiskConfig())

    screen = gate.screen(((_stock("FIRST", 0.20), _stock("SECOND", 0.10)),), 1)

    assert tuple(stock.symbol for stock in screen.selected) == ("FIRST",)
    assert tuple(stock.symbol for stock in screen.not_selected) == ("SECOND",)
    assert screen.rejected == ()

    path = tmp_path / "market_risk_screen.csv"
    write_market_risk_screen(path, screen)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert tuple(row["symbol"] for row in rows) == ("FIRST", "SECOND")
    assert rows[1]["reason"] == "포트폴리오 한도"


def test_halt_feed_fails_closed_when_schema_changes() -> None:
    with httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, text="symbol,status\nHALT,H\n", request=request)
        )
    ) as client, pytest.raises(HaltFeedFormatError):
        _ = fetch_active_halts(client)


def test_market_risk_screen_writes_append_only_decision_rows(tmp_path: Path) -> None:
    gate = MarketRiskGate(_empty_halts(), MarketRiskConfig())
    screen = gate.screen(
        ((_stock("MISSING", 0.20, quote=(0.0, 0.0)), _stock("SAFE", 0.10)),),
        1,
    )
    path = tmp_path / "market_risk_screen.csv"

    write_market_risk_screen(path, screen)
    write_market_risk_screen(path, screen)

    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 4
    assert tuple(row["symbol"] for row in rows[:2]) == ("SAFE", "MISSING")
    assert tuple(row["selected"] for row in rows[:2]) == ("True", "False")
    assert rows[1]["reason"] == RiskRejectReason.MISSING_QUOTE.value
    assert rows[0]["volume"] == "1000000"
    assert rows[0]["average_daily_volume"] == "1000000"
    assert rows[0]["volume_to_adv"] == "1.0"


def test_market_risk_writer_migrates_legacy_header_before_append(tmp_path: Path) -> None:
    path = tmp_path / "market_risk_screen.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER[:-3])
        writer.writerow(
            (
                "2026-07-10T13:30:00+00:00", "NAS", "OLD", True, "", 0.1,
                10.0, 9.99, 10.01, 20.0, 60.0, 1_000_000.0,
            )
        )
    screen = MarketRiskGate(_empty_halts(), MarketRiskConfig()).screen(((_stock("NEW", 0.10),),), 1)

    write_market_risk_screen(path, screen)

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = tuple(reader)
    assert tuple(reader.fieldnames or ()) == MARKET_RISK_HEADER
    assert tuple(row["symbol"] for row in rows) == ("OLD", "NEW")
    assert rows[0]["volume"] == ""
    assert rows[1]["volume_to_adv"] == "1.0"


def _stock(
    symbol: str,
    change_pct: float,
    *,
    quote: tuple[float, float] = (9.99, 10.01),
) -> KisRankedStock:
    return KisRankedStock(
        "NAS",
        symbol,
        symbol.title(),
        10.0,
        change_pct,
        quote[0],
        quote[1],
        1_000_000,
        10_000_000.0,
        1_000_000,
        1,
    )


def _empty_halts() -> HaltSnapshot:
    return HaltSnapshot(dt.datetime(2026, 7, 13, 5, 0, tzinfo=dt.UTC), frozenset())
