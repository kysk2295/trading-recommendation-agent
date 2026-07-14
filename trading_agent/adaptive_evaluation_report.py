from __future__ import annotations

from pathlib import Path

from trading_agent.adaptive_evaluation_models import AdaptiveEvaluation, WindowEvidence


def write_adaptive_evaluation(output_dir: Path, result: AdaptiveEvaluation) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "adaptive_evaluation.json"
    temporary = destination.with_suffix(".tmp")
    _ = temporary.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    _ = (output_dir / "adaptive_evaluation_ko.md").write_text(
        _markdown(result),
        encoding="utf-8",
    )


def _markdown(result: AdaptiveEvaluation) -> str:
    lines = [
        "# 적응형 급등주 전략 평가 카드",
        "",
        "> 확정 수익이 아닌 shadow Paper 전진검증 판단입니다.",
        "",
        f"- 기준일: {result.as_of}",
        f"- 전략 버전: {result.strategy_version}",
        f"- 권고: `{result.action.value}`",
        f"- 근거: {', '.join(result.reasons)}",
        "- 자동 상태 변경: 금지",
        "- 60일은 수익 확정이 아니라 최종 검토 문턱이며, 명확한 실패는 5일에도 중단할 수 있습니다.",
        "",
        "## 롤링 성과",
        "",
        "| 창 | 적격일 | 거래 | PF | 평균 | MDD | 평균 95% CI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_window_row(row) for row in result.windows)
    lines.extend(("", "## 시장 국면", "", f"- 사전분류 coverage: {result.regime_coverage:.1%}"))
    lines.extend(
        f"- `{row.regime}`: {row.session_count}일/{row.trade_count}거래, "
        + f"PF {_number(row.profit_factor)}, 평균 {_percent(row.average_return)}"
        for row in result.regimes
    )
    if not result.regimes:
        lines.append("- 사전 시점 국면 라벨 없음: 최종 증명에는 사용 불가")
    lines.extend(("", "## 최종 검토 차단 사유", ""))
    lines.extend(f"- {blocker}" for blocker in result.proof_blockers)
    if not result.proof_blockers:
        lines.append("- 연구 통계 문턱 충족; 별도 주문·안전 승인 필요")
    return "\n".join(lines) + "\n"


def _window_row(row: WindowEvidence) -> str:
    observed = f"{row.observed_sessions}/{row.required_sessions}"
    return (
        f"| {row.required_sessions}일 | {observed} | {row.trade_count} | "
        + f"{_number(row.profit_factor)} | {_percent(row.average_return)} | "
        + f"{_percent(row.max_drawdown)} | {_percent(row.mean_ci_low)} ~ {_percent(row.mean_ci_high)} |"
    )


def _number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"
