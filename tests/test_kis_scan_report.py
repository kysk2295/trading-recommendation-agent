from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.kis_auth import KisMode
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import ScanObservation
from trading_agent.kis_scan_report import ScanSummary, write_scan_summary
from trading_agent.market_risk import (
    MarketRiskConfig,
    MarketRiskRejection,
    MarketRiskScreen,
    RiskRejectReason,
)
from trading_agent.ranking_journal import RankingFailure, RankingSource
from trading_agent.strategy_factory import StrategyMode


def test_scan_report_discloses_risk_gate_and_missing_float(tmp_path: Path) -> None:
    observed_at = dt.datetime(2026, 7, 13, 5, 0, tzinfo=dt.UTC)
    missing = KisRankedStock(
        "NAS",
        "MISSING",
        "Missing",
        10.0,
        0.1,
        0.0,
        0.0,
        100_000,
        1_000_000.0,
        100_000,
        1,
    )
    screen = MarketRiskScreen(
        observed_at,
        MarketRiskConfig(),
        (),
        (),
        (
            MarketRiskRejection(
                missing,
                RiskRejectReason.MISSING_QUOTE,
                float("inf"),
            ),
        ),
    )
    summary = ScanSummary(
        observed_at,
        KisMode.LIVE,
        StrategyMode.GAP_AND_GO,
        31,
        screen,
        (ScanObservation("NAS", "SAFE", 0.1, 10.0, 20.0, 0, "시장 폐장"),),
        0,
    )
    path = tmp_path / "summary.md"

    write_scan_summary(path, summary)

    report = path.read_text(encoding="utf-8")
    assert "공식 현재 거래정지 종목: 31개" in report
    assert "MISSING | 유효 호가 없음" in report
    assert "PIT float: 미제공" in report


def test_scan_report_marks_partial_ranking_universe(tmp_path: Path) -> None:
    # Given: one candidate source was unavailable but downstream evaluation ran.
    observed_at = dt.datetime(2026, 7, 14, 10, 30, tzinfo=dt.UTC)
    summary = ScanSummary(
        observed_at,
        KisMode.LIVE,
        StrategyMode.ORB,
        0,
        MarketRiskScreen(observed_at, MarketRiskConfig(), (), (), ()),
        (),
        0,
        (RankingFailure(RankingSource.UPDOWN, "AMS", "HTTP 500"),),
    )
    path = tmp_path / "summary.md"

    # When: the user-facing scan report is written.
    write_scan_summary(path, summary)

    # Then: it cannot be mistaken for a complete all-exchange scan.
    report = path.read_text(encoding="utf-8")
    assert "랭킹 요청 실패: 1개" in report
    assert "부분 모집단" in report
    assert "AMS/updown: HTTP 500" in report
