from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter, ValidationError

from trading_agent.alpaca_scanner_quality_models import ScannerQualityOutcome


class ScannerQualityGateConfigError(ValueError):
    pass


class ScannerQualityGateReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScannerQualityGateConfig:
    minimum_path_coverage: float = 0.8
    minimum_complete_candidate_days: int = 100

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_path_coverage <= 1.0:
            raise ScannerQualityGateConfigError("minimum path coverage must be between 0 and 1")
        if self.minimum_complete_candidate_days < 0:
            raise ScannerQualityGateConfigError("minimum complete candidate days cannot be negative")


@dataclass(frozen=True, slots=True)
class ScannerQualityGateResult:
    passed: bool
    unique_candidate_days: int
    complete_candidate_days: int
    path_coverage: float
    minimum_path_coverage: float
    minimum_complete_candidate_days: int
    issues: tuple[str, ...]


DEFAULT_SCANNER_QUALITY_GATE_CONFIG: Final = ScannerQualityGateConfig()
SCANNER_QUALITY_GATE_ADAPTER: Final = TypeAdapter(ScannerQualityGateResult)


def evaluate_scanner_quality_gate(
    outcomes: tuple[ScannerQualityOutcome, ...],
    config: ScannerQualityGateConfig = DEFAULT_SCANNER_QUALITY_GATE_CONFIG,
) -> ScannerQualityGateResult:
    candidate_days = {(row.session_date, row.symbol) for row in outcomes}
    complete_days = {(row.session_date, row.symbol) for row in outcomes if row.complete}
    coverage = len(complete_days) / len(candidate_days) if candidate_days else 0.0
    issues: list[str] = []
    if coverage < config.minimum_path_coverage:
        issues.append(f"path_coverage:{coverage:.6f}<{config.minimum_path_coverage:.6f}")
    if len(complete_days) < config.minimum_complete_candidate_days:
        issues.append(f"complete_candidate_days:{len(complete_days)}<{config.minimum_complete_candidate_days}")
    return ScannerQualityGateResult(
        passed=not issues,
        unique_candidate_days=len(candidate_days),
        complete_candidate_days=len(complete_days),
        path_coverage=coverage,
        minimum_path_coverage=config.minimum_path_coverage,
        minimum_complete_candidate_days=config.minimum_complete_candidate_days,
        issues=tuple(issues),
    )


def write_scanner_quality_gate(
    output_dir: Path,
    result: ScannerQualityGateResult,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "scanner_quality_gate.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    lines = (
        "# Alpaca 스캐너 데이터 게이트",
        "",
        "> 수익성이나 PF가 아니라 3년 확장 전 경로 완전성 판정입니다.",
        "",
        f"- 판정: {'PASS' if result.passed else 'FAIL'}",
        f"- 고유 후보-일: {result.unique_candidate_days}",
        f"- 완전 후보-일: {result.complete_candidate_days}",
        f"- 경로 커버리지: {result.path_coverage:.2%}",
        f"- 사전 기준: 완전 후보-일 {result.minimum_complete_candidate_days}개 이상, "
        + f"경로 {result.minimum_path_coverage:.0%} 이상",
        f"- 문제: {', '.join(result.issues) if result.issues else '없음'}",
    )
    _ = (output_dir / "scanner_quality_gate_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def read_scanner_quality_gate(path: Path) -> ScannerQualityGateResult:
    try:
        return SCANNER_QUALITY_GATE_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise ScannerQualityGateReadError(f"invalid scanner quality gate: {path}: {error}") from error
