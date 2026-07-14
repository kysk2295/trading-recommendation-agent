from __future__ import annotations

import csv
from pathlib import Path
from typing import Final

from trading_agent.candidate_persistence import PersistenceResult

CANDIDATE_HEADER: Final = (
    "canonical_exchange",
    "symbol",
    "first_observed_at",
    "last_observed_at",
    "observed_snapshot_count",
    "eligible_snapshot_count",
    "selected_snapshot_count",
    "maximum_change_pct",
)
TRANSITION_HEADER: Final = (
    "source_observed_at",
    "destination_observed_at",
    "source_eligible_count",
    "destination_eligible_count",
    "continued_count",
    "continuation_rate",
    "jaccard",
)


def write_candidate_persistence(output: Path, result: PersistenceResult) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "candidate_persistence_candidates.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(CANDIDATE_HEADER)
        writer.writerows(
            (
                row.canonical_exchange,
                row.symbol,
                row.first_observed_at.isoformat(),
                row.last_observed_at.isoformat(),
                row.observed_snapshot_count,
                row.eligible_snapshot_count,
                row.selected_snapshot_count,
                row.maximum_change_pct,
            )
            for row in result.candidates
        )
    with (output / "candidate_persistence_transitions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(TRANSITION_HEADER)
        writer.writerows(
            (
                row.source_observed_at.isoformat(),
                row.destination_observed_at.isoformat(),
                row.source_eligible_count,
                row.destination_eligible_count,
                row.continued_count,
                row.continuation_rate,
                row.jaccard,
            )
            for row in result.transitions
        )
    summary = result.summary
    lines = [
        "# 급등주 후보 스냅숏 지속성 진단",
        "",
        "> 전체 위험판정 모집단의 시점 간 재등장 진단이며 수익성 결과가 아니다.",
        "",
        f"- 스냅숏: {summary.snapshot_count}개",
        f"- 고유 후보: {summary.candidate_count}개",
        f"- 위험적격 관측: {summary.risk_eligible_occurrences}건",
        f"- 실제 선정 관측: {summary.selected_occurrences}건",
        f"- 평균 다음 스냅숏 유지율: {_rate(summary.mean_continuation_rate)}",
        f"- 평균 Jaccard: {_rate(summary.mean_jaccard)}",
        "",
        "| 출발 시각 | 도착 시각 | 출발 적격 | 도착 적격 | 재등장 | 유지율 | Jaccard |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                row.source_observed_at.isoformat(),
                row.destination_observed_at.isoformat(),
                str(row.source_eligible_count),
                str(row.destination_eligible_count),
                str(row.continued_count),
                _rate(row.continuation_rate),
                _rate(row.jaccard),
            )
        )
        + " |"
        for row in result.transitions
    )
    lines.extend(
        (
            "",
            "`포트폴리오 한도` 후보는 위험적격으로 유지하고 실제 위험 제외만 전환 집합에서 뺐다.",
            "높은 재등장률은 후보 안정성일 뿐 이후 장중 수익이나 체결 가능성을 뜻하지 않는다.",
        )
    )
    _ = (output / "candidate_persistence_report_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _rate(value: float | None) -> str:
    return "" if value is None else f"{value:.2%}"
