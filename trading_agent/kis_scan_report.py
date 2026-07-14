from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_auth import KisMode
from trading_agent.kis_scan import ScanObservation
from trading_agent.market_risk import MarketRiskScreen
from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class ScanSummary:
    checked_at: dt.datetime
    mode: KisMode
    strategy: StrategyMode
    active_halt_count: int
    risk_screen: MarketRiskScreen
    observations: tuple[ScanObservation, ...]
    recommendation_count: int


def write_scan_summary(path: Path, summary: ScanSummary) -> None:
    lines = [
        "# KIS 미국 급등주 Paper 스캔",
        "",
        "> 주문·잔고 API를 사용하지 않는 연구용 시세 분석입니다.",
        "",
        f"- 확인 시각: {summary.checked_at.isoformat(timespec='seconds')}",
        f"- 시세 환경: {summary.mode.value}",
        f"- 전략: {summary.strategy.value}",
        f"- 조건부 추천: {summary.recommendation_count}개",
        f"- 공식 현재 거래정지 종목: {summary.active_halt_count}개",
        f"- 위험 게이트 제외: {len(summary.risk_screen.rejected)}개",
        f"- 위험 통과 후 포트폴리오 한도 제외: {len(summary.risk_screen.not_selected)}개",
        "- 예상 왕복비용: 현재 spread + 편도 20bp 슬리피지 예비비",
        "- PIT float: 미제공, 거래대금은 저유동성 대리필터일 뿐 float가 아님",
        "",
        "| 거래소 | 종목 | 등락률 | 가격 | 스프레드(bp) | 정규장 분봉 | 상태 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    lines.extend(
        f"| {row.exchange} | {row.symbol} | {row.change_pct:.2%} | "
        + f"{row.price:.4f} | {_spread(row.spread_bps)} | {row.bars} | "
        + f"{row.status} |"
        for row in summary.observations
    )
    if not summary.observations:
        lines.append("| - | - | - | - | - | - | 조건 충족 후보 없음 |")
    lines.extend(
        (
            "",
            "## 위험 게이트 제외",
            "",
            "| 종목 | 사유 | 스프레드(bp) | 예상 왕복비용(bp) |",
            "|---:|---|---:|---:|",
        )
    )
    lines.extend(
        f"| {row.stock.symbol} | {row.reason.value} | "
        + f"{_spread(row.stock.spread_bps)} | "
        + f"{_spread(row.estimated_round_trip_cost_bps)} |"
        for row in summary.risk_screen.rejected[:10]
    )
    if not summary.risk_screen.rejected:
        lines.append("| - | 제외 없음 | - | - |")
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _spread(value: float) -> str:
    return f"{value:.2f}" if math.isfinite(value) else "없음"
