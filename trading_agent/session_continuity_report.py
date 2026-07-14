from __future__ import annotations

import csv
from pathlib import Path
from typing import Final

from trading_agent.session_continuity import (
    CandidateContinuity,
    ContinuityResult,
    PhaseStats,
)

CANDIDATE_HEADER: Final = (
    "canonical_exchange",
    "symbol",
    "daytime_first_observed_at",
    "premarket_first_observed_at",
    "regular_first_observed_at",
    "daytime_risk_eligible",
    "premarket_risk_eligible",
    "regular_risk_eligible",
    "daytime_selected",
    "premarket_selected",
    "regular_selected",
    "daytime_maximum_change_pct",
    "premarket_maximum_change_pct",
    "regular_maximum_change_pct",
    "daytime_to_premarket",
    "premarket_to_regular",
    "daytime_to_regular",
)
SUMMARY_HEADER: Final = (
    "source_phase",
    "destination_phase",
    "source_eligible_count",
    "destination_eligible_count",
    "continued_count",
    "continuation_rate",
)


def write_continuity_outputs(output: Path, result: ContinuityResult) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "session_continuity_candidates.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(CANDIDATE_HEADER)
        writer.writerows(_candidate_row(candidate) for candidate in result.candidates)
    with (output / "session_continuity_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(SUMMARY_HEADER)
        writer.writerows(
            (
                summary.source_phase.value,
                summary.destination_phase.value,
                summary.source_eligible_count,
                summary.destination_eligible_count,
                summary.continued_count,
                summary.continuation_rate,
            )
            for summary in result.summaries
        )
    lines = [
        "# KIS 세션 후보 연속성 진단",
        "",
        "> 이 표는 전체 위험판정 후보의 세션 간 재등장률이며 수익성 결과가 아니다.",
        "",
        f"- 전체 고유 후보: {len(result.candidates)}개",
        "- `BAQ/BAY/BAA`는 각각 `NAS/NYS/AMS`로 매핑했다.",
        "- 포트폴리오 한도 후보는 위험 적격으로 유지하고 실제 위험 제외는 분모에서 뺐다.",
        "- 미래 세션이 아직 없으면 연속률을 공란으로 두며 0수익으로 바꾸지 않는다.",
        "",
        "| 출발 세션 | 도착 세션 | 출발 적격 | 도착 적격 | 연속 | 연속률 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                summary.source_phase.value,
                summary.destination_phase.value,
                str(summary.source_eligible_count),
                str(summary.destination_eligible_count),
                str(summary.continued_count),
                (
                    ""
                    if summary.continuation_rate is None
                    else f"{summary.continuation_rate:.2%}"
                ),
            )
        )
        + " |"
        for summary in result.summaries
    )
    lines.extend(
        (
            "",
            "정규장 완료 분봉·opening gap·체결 결과를 결합하기 전에는 후보 품질을 수익으로 해석하지 않는다.",
        )
    )
    _ = (output / "session_continuity_report_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _candidate_row(candidate: CandidateContinuity) -> tuple[str | bool | float, ...]:
    return (
        candidate.canonical_exchange,
        candidate.symbol,
        _timestamp(candidate.daytime),
        _timestamp(candidate.premarket),
        _timestamp(candidate.regular),
        _eligible(candidate.daytime),
        _eligible(candidate.premarket),
        _eligible(candidate.regular),
        _selected(candidate.daytime),
        _selected(candidate.premarket),
        _selected(candidate.regular),
        _change(candidate.daytime),
        _change(candidate.premarket),
        _change(candidate.regular),
        candidate.daytime_to_premarket,
        candidate.premarket_to_regular,
        candidate.daytime_to_regular,
    )


def _timestamp(stats: PhaseStats | None) -> str:
    return "" if stats is None else stats.first_observed_at.isoformat()


def _eligible(stats: PhaseStats | None) -> str | bool:
    return "" if stats is None else stats.risk_eligible


def _selected(stats: PhaseStats | None) -> str | bool:
    return "" if stats is None else stats.selected


def _change(stats: PhaseStats | None) -> str | float:
    return "" if stats is None else stats.maximum_change_pct
