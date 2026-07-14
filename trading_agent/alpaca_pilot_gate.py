from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from trading_agent.alpaca_pilot_audit import PilotAuditResult
from trading_agent.alpaca_scanner_quality_gate import ScannerQualityGateResult
from trading_agent.orb_artifact_gate import OrbArtifactGateResult
from trading_agent.scanner_artifact_gate import ScannerArtifactGateResult

REQUIRED_SCANNER_PATH_COVERAGE: Final = 0.8
REQUIRED_COMPLETE_CANDIDATE_DAYS: Final = 100


@dataclass(frozen=True, slots=True)
class AlpacaPilotGateResult:
    passed: bool
    audit_passed: bool
    audit_session_count: int
    scanner_gate_passed: bool
    scanner_thresholds_sufficient: bool
    scanner_artifacts_passed: bool
    scanner_summary_row_count: int
    scanner_outcome_row_count: int
    scanner_yearly_row_count: int
    expected_scanner_summary_row_count: int
    unique_candidate_days: int
    complete_candidate_days: int
    path_coverage: float
    scanner_minimum_path_coverage: float
    scanner_minimum_complete_candidate_days: int
    orb_executed: bool
    orb_outcome_count: int
    orb_config_count: int
    expected_orb_config_count: int
    orb_artifacts_passed: bool
    orb_parameter_row_count: int
    orb_period_row_count: int
    orb_flatness_row_count: int
    expected_orb_parameter_row_count: int
    expected_orb_period_row_count: int
    expected_orb_flatness_row_count: int
    issues: tuple[str, ...]


def evaluate_alpaca_pilot_gate(
    audit: PilotAuditResult,
    scanner: ScannerQualityGateResult,
    *,
    orb_executed: bool,
    orb_outcome_count: int = 0,
    orb_config_count: int = 0,
    expected_orb_config_count: int = 81,
    orb_artifacts: OrbArtifactGateResult | None = None,
    scanner_artifacts: ScannerArtifactGateResult | None = None,
) -> AlpacaPilotGateResult:
    issues: list[str] = []
    thresholds_sufficient = True
    if not audit.passed:
        issues.extend(f"audit:{issue}" for issue in audit.issues)
    if not scanner.passed:
        issues.extend(f"scanner:{issue}" for issue in scanner.issues)
    scanner_artifacts_passed = scanner_artifacts is not None and scanner_artifacts.passed
    if scanner_artifacts is None:
        issues.append("scanner_artifact:not_audited")
    elif not scanner_artifacts.passed:
        issues.extend(f"scanner_artifact:{issue}" for issue in scanner_artifacts.issues)
    if scanner.minimum_path_coverage < REQUIRED_SCANNER_PATH_COVERAGE:
        thresholds_sufficient = False
        issues.append(
            "scanner:minimum_path_coverage:"
            + f"{scanner.minimum_path_coverage:.6f}<{REQUIRED_SCANNER_PATH_COVERAGE:.6f}"
        )
    if scanner.minimum_complete_candidate_days < REQUIRED_COMPLETE_CANDIDATE_DAYS:
        thresholds_sufficient = False
        issues.append(
            "scanner:minimum_complete_candidate_days:"
            + f"{scanner.minimum_complete_candidate_days}<{REQUIRED_COMPLETE_CANDIDATE_DAYS}"
        )
    if audit.passed and scanner.passed and thresholds_sufficient and scanner_artifacts_passed:
        if not orb_executed:
            issues.append("orb:not_executed")
        else:
            if orb_outcome_count <= 0:
                issues.append("orb:outcome_count=0")
            if orb_config_count != expected_orb_config_count:
                issues.append(f"orb:config_count:{orb_config_count}!={expected_orb_config_count}")
            if orb_artifacts is None:
                issues.append("orb:artifacts:not_audited")
            elif not orb_artifacts.passed:
                issues.extend(f"orb_artifact:{issue}" for issue in orb_artifacts.issues)
    expected_parameter_rows = expected_orb_config_count * 3
    expected_period_rows = expected_parameter_rows * 2
    expected_flatness_rows = expected_parameter_rows
    return AlpacaPilotGateResult(
        passed=not issues,
        audit_passed=audit.passed,
        audit_session_count=audit.session_count,
        scanner_gate_passed=scanner.passed,
        scanner_thresholds_sufficient=thresholds_sufficient,
        scanner_artifacts_passed=scanner_artifacts_passed,
        scanner_summary_row_count=(scanner_artifacts.summary_row_count if scanner_artifacts is not None else 0),
        scanner_outcome_row_count=(scanner_artifacts.outcome_row_count if scanner_artifacts is not None else 0),
        scanner_yearly_row_count=(scanner_artifacts.yearly_row_count if scanner_artifacts is not None else 0),
        expected_scanner_summary_row_count=(
            scanner_artifacts.expected_summary_row_count if scanner_artifacts is not None else 108
        ),
        unique_candidate_days=scanner.unique_candidate_days,
        complete_candidate_days=scanner.complete_candidate_days,
        path_coverage=scanner.path_coverage,
        scanner_minimum_path_coverage=scanner.minimum_path_coverage,
        scanner_minimum_complete_candidate_days=scanner.minimum_complete_candidate_days,
        orb_executed=orb_executed,
        orb_outcome_count=orb_outcome_count,
        orb_config_count=orb_config_count,
        expected_orb_config_count=expected_orb_config_count,
        orb_artifacts_passed=orb_artifacts.passed if orb_artifacts is not None else False,
        orb_parameter_row_count=orb_artifacts.parameter_row_count if orb_artifacts is not None else 0,
        orb_period_row_count=orb_artifacts.period_row_count if orb_artifacts is not None else 0,
        orb_flatness_row_count=orb_artifacts.flatness_row_count if orb_artifacts is not None else 0,
        expected_orb_parameter_row_count=(
            orb_artifacts.expected_parameter_row_count if orb_artifacts is not None else expected_parameter_rows
        ),
        expected_orb_period_row_count=(
            orb_artifacts.expected_period_row_count if orb_artifacts is not None else expected_period_rows
        ),
        expected_orb_flatness_row_count=(
            orb_artifacts.expected_flatness_row_count if orb_artifacts is not None else expected_flatness_rows
        ),
        issues=tuple(issues),
    )


def write_alpaca_pilot_gate(
    report_path: Path,
    result: AlpacaPilotGateResult,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = (
        report_path.with_name(report_path.name.removesuffix("_ko.md") + ".json")
        if report_path.name.endswith("_ko.md")
        else report_path.with_suffix(".json")
    )
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    lines = (
        "# Alpaca 3개월 파일럿 게이트",
        "",
        "> 수익이나 PF가 아니라 3년 확장 전 데이터·구현 판정입니다.",
        "",
        f"- 판정: {'PASS' if result.passed else 'FAIL'}",
        f"- 실제 거래일 감사: {'PASS' if result.audit_passed else 'FAIL'} ({result.audit_session_count}세션)",
        f"- 스캐너 데이터 게이트: {'PASS' if result.scanner_gate_passed else 'FAIL'}",
        f"- 스캐너 기준 강도: {'PASS' if result.scanner_thresholds_sufficient else 'FAIL'}",
        f"- 스캐너 산출물 계약: {'PASS' if result.scanner_artifacts_passed else 'FAIL'}",
        f"- 스캐너 요약/outcome/yearly 행: {result.scanner_summary_row_count}/"
        + f"{result.scanner_outcome_row_count}/{result.scanner_yearly_row_count}",
        f"- 고유/완전 후보-일: {result.unique_candidate_days}/{result.complete_candidate_days}",
        f"- 경로 커버리지: {result.path_coverage:.2%}",
        f"- 스캐너 사전 기준: 경로 {result.scanner_minimum_path_coverage:.0%}, "
        + f"완전 후보-일 {result.scanner_minimum_complete_candidate_days}개",
        f"- ORB 실행: {'완료' if result.orb_executed else '미실행'}",
        f"- ORB outcome/설정: {result.orb_outcome_count}/{result.orb_config_count}",
        f"- ORB 산출물 계약: {'PASS' if result.orb_artifacts_passed else 'FAIL'}",
        f"- 파라미터/기간/평탄성 행: {result.orb_parameter_row_count}/"
        + f"{result.orb_period_row_count}/{result.orb_flatness_row_count}",
        f"- 문제: {', '.join(result.issues) if result.issues else '없음'}",
    )
    _ = report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
