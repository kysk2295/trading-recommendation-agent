from __future__ import annotations

import csv
from pathlib import Path

from trading_agent.risk_sensitivity import (
    RiskSensitivityResult,
    RiskSensitivitySelection,
    RiskSensitivitySummary,
)


def write_risk_sensitivity_report(
    output_dir: Path,
    result: RiskSensitivityResult,
    source_paths: tuple[Path, ...],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_summaries(output_dir / "market_risk_sensitivity.csv", result.summaries)
    _write_selections(output_dir / "market_risk_selected_candidates.csv", result.selections)
    _write_report(
        output_dir / "market_risk_sensitivity_ko.md",
        result,
        source_paths,
    )


def _write_summaries(path: Path, summaries: tuple[RiskSensitivitySummary, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "max_spread_bps",
                "slippage_per_side_bps",
                "max_round_trip_cost_bps",
                "snapshot_count",
                "candidate_count",
                "hard_excluded_count",
                "cost_eligible_count",
                "selected_count",
                "retention_rate",
            )
        )
        for row in summaries:
            writer.writerow(
                (
                    row.config.max_spread_bps,
                    row.config.slippage_per_side_bps,
                    row.config.max_round_trip_cost_bps,
                    row.snapshot_count,
                    row.candidate_count,
                    row.hard_excluded_count,
                    row.cost_eligible_count,
                    row.selected_count,
                    row.retention_rate,
                )
            )


def _write_selections(path: Path, rows: tuple[RiskSensitivitySelection, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "max_spread_bps",
                "slippage_per_side_bps",
                "max_round_trip_cost_bps",
                "observed_at",
                "rank",
                "exchange",
                "symbol",
                "change_pct",
                "spread_bps",
                "estimated_round_trip_cost_bps",
                "dollar_volume",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row.config.max_spread_bps,
                    row.config.slippage_per_side_bps,
                    row.config.max_round_trip_cost_bps,
                    row.observed_at.isoformat(),
                    row.rank,
                    row.exchange,
                    row.symbol,
                    row.change_pct,
                    row.spread_bps,
                    row.estimated_round_trip_cost_bps,
                    row.dollar_volume,
                )
            )


def _write_report(
    path: Path,
    result: RiskSensitivityResult,
    source_paths: tuple[Path, ...],
) -> None:
    lines = [
        "# KIS 시장위험 인접값 민감도",
        "",
        "> 이 결과는 후보 보존율 분석이며 수익성 백테스트가 아니다.",
        "",
        f"- 입력 파일: {len(source_paths)}개",
        f"- 시험 조합: {len(result.summaries)}개 인접 조합",
        "- 포트폴리오: 각 스냅샷에서 위험·비용 필터 후 등락률/거래대금 순 최대 10개 재선정",
        "- 거래정지·유효 호가 없음·역전 호가는 모든 조합에서 고정 제외",
        "",
        "| 최대 spread(bp) | 편도 slippage(bp) | 최대 왕복비용(bp) | 후보 | 고정 제외 | 비용 통과 | 선택 | 보존율 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {row.config.max_spread_bps:.0f} | {row.config.slippage_per_side_bps:.0f} | "
        + f"{row.config.max_round_trip_cost_bps:.0f} | {row.candidate_count} | "
        + f"{row.hard_excluded_count} | {row.cost_eligible_count} | {row.selected_count} | "
        + f"{row.retention_rate:.1%} |"
        for row in result.summaries
    )
    lines.extend(
        (
            "",
            "## 해석 한계",
            "",
            "단일 또는 소수 시점의 후보 통과율은 전략 PF·승률·기대수익을 말하지 않는다. "
            "정규장 전진 관찰과 분봉 경로가 누적된 뒤 별도로 평가해야 한다.",
        )
    )
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
