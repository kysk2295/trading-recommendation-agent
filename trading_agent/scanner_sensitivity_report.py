from __future__ import annotations

import csv
from pathlib import Path

from trading_agent.scanner_sensitivity import (
    ScannerSensitivityResult,
    ScannerSensitivitySelection,
    ScannerSensitivitySummary,
)


def write_scanner_sensitivity_report(
    output_dir: Path,
    result: ScannerSensitivityResult,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_summaries(output_dir / "scanner_candidate_sensitivity.csv", result.summaries)
    _write_selections(output_dir / "scanner_candidate_selections.csv", result.selections)
    _write_report(output_dir / "scanner_candidate_sensitivity_ko.md", result)


def _write_summaries(path: Path, rows: tuple[ScannerSensitivitySummary, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "min_change_pct",
                "max_price",
                "min_dollar_volume",
                "min_volume_to_adv",
                "snapshot_count",
                "candidate_count",
                "risk_eligible_count",
                "feature_available_count",
                "feature_missing_count",
                "threshold_eligible_count",
                "selected_count",
                "retention_rate",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row.config.min_change_pct,
                    row.config.max_price,
                    row.config.min_dollar_volume,
                    row.config.min_volume_to_adv,
                    row.snapshot_count,
                    row.candidate_count,
                    row.risk_eligible_count,
                    row.feature_available_count,
                    row.feature_missing_count,
                    row.threshold_eligible_count,
                    row.selected_count,
                    row.retention_rate,
                )
            )


def _write_selections(path: Path, rows: tuple[ScannerSensitivitySelection, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "min_change_pct",
                "max_price",
                "min_dollar_volume",
                "min_volume_to_adv",
                "observed_at",
                "rank",
                "exchange",
                "symbol",
                "change_pct",
                "price",
                "dollar_volume",
                "volume_to_adv",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row.config.min_change_pct,
                    row.config.max_price,
                    row.config.min_dollar_volume,
                    row.config.min_volume_to_adv,
                    row.observed_at.isoformat(),
                    row.rank,
                    row.exchange,
                    row.symbol,
                    row.change_pct,
                    row.price,
                    row.dollar_volume,
                    row.volume_to_adv,
                )
            )


def _write_report(path: Path, result: ScannerSensitivityResult) -> None:
    lines = [
        "# KIS 급등 스캐너 후보 인접값 진단",
        "",
        "> 전체 위험판정 후보의 보존·재선정 진단이며 수익성·후행수익 분석이 아니다.",
        "",
        f"- 시험 조합: {len(result.summaries)}개",
        "- 등락률: 4/6/8%",
        "- 최대 가격: 20/50/200달러",
        "- 최소 거래대금: 0.5/1/2백만 달러",
        "- 시점 누적 거래량/ADV: 0.05/0.10/0.20",
        "- 각 스냅샷에서 위험 통과 후 상승률·거래대금 순 최대 10개 재선정",
        "- opening gap: 전체 후보 시가 미제공으로 현재 계산하지 않음",
        "",
        "| 상승률 | 최대가격 | 거래대금 | volume/ADV | 위험통과 | 특징가용 | 임계통과 | 선택 | 보존율 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {row.config.min_change_pct:.0%} | ${row.config.max_price:.0f} | "
        + f"${row.config.min_dollar_volume / 1_000_000:.1f}M | {row.config.min_volume_to_adv:.2f} | "
        + f"{row.risk_eligible_count} | {row.feature_available_count} | "
        + f"{row.threshold_eligible_count} | {row.selected_count} | {row.retention_rate:.1%} |"
        for row in result.summaries
    )
    lines.extend(
        (
            "",
            "## 해석 제한",
            "",
            "KIS 랭킹 상위 표본은 미국 전체 종목 PIT 모집단이 아니다. volume/ADV는 스캔 시점 누적 거래량을 "
            "일평균 거래량으로 나눈 값이며 장중 시간대 보정 RVOL이 아니다. 정규장 후행 가격·분봉 경로가 "
            "없는 표본에서 PF·승률·기대수익을 계산하지 않는다.",
        )
    )
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
