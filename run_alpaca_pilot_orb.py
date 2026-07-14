#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "rich>=13.9", "typer>=0.15"]
# ///
# How to run:
# ./run_alpaca_pilot_orb.py <staged-root> --output-dir <result-dir>

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import typer
from rich import print as rprint

from trading_agent.alpaca_orb_archive import (
    AlpacaOrbArchiveConfig,
    analyze_alpaca_orb_grid,
)
from trading_agent.alpaca_pilot_audit import PilotAuditResult, audit_staged_pilot
from trading_agent.alpaca_pilot_audit_report import write_pilot_audit
from trading_agent.alpaca_pilot_gate import (
    evaluate_alpaca_pilot_gate,
    write_alpaca_pilot_gate,
)
from trading_agent.alpaca_scanner_quality_gate import (
    ScannerQualityGateReadError,
    ScannerQualityGateResult,
    read_scanner_quality_gate,
)
from trading_agent.orb_analysis import default_orb_grid
from trading_agent.orb_artifact_gate import audit_orb_report_artifacts
from trading_agent.orb_report import write_orb_report
from trading_agent.scanner_artifact_gate import (
    ScannerArtifactGateResult,
    audit_scanner_report_artifacts,
)
from trading_agent.session_date_range import SessionDateRange

ASSUMED_SPREAD_BPS: Final = 20.0


@dataclass(frozen=True, slots=True)
class PilotRunConfig:
    minimum_sessions: int = 50
    assumed_spread_bps: float = ASSUMED_SPREAD_BPS
    max_positions: int = 10
    session_range: SessionDateRange | None = None


def main(
    source_dir: str,
    output_dir: str | None = None,
    minimum_sessions: int = 50,
    scanner_gate_path: str | None = None,
    pilot_gate_path: str | None = None,
    start: Annotated[dt.date | None, typer.Option(parser=dt.date.fromisoformat)] = None,
    end: Annotated[dt.date | None, typer.Option(parser=dt.date.fromisoformat)] = None,
) -> None:
    source = Path(source_dir)
    if not source.is_dir():
        raise typer.BadParameter(f"Alpaca 단계형 아카이브를 찾을 수 없습니다: {source}")
    output = source / "pilot_orb" if output_dir is None else Path(output_dir)
    scanner_path = (
        source / "pilot_scanner_quality/scanner_quality_gate.json"
        if scanner_gate_path is None
        else Path(scanner_gate_path)
    )
    report_path = output / "pilot_gate_ko.md" if pilot_gate_path is None else Path(pilot_gate_path)
    try:
        scanner_gate = read_scanner_quality_gate(scanner_path)
    except ScannerQualityGateReadError as error:
        raise typer.BadParameter(str(error)) from None
    try:
        session_range = SessionDateRange.optional(start, end)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    scanner_artifacts = audit_scanner_report_artifacts(
        scanner_path.parent,
        expected_config_count=108,
    )
    run_pilot(
        source,
        output,
        PilotRunConfig(minimum_sessions=minimum_sessions, session_range=session_range),
        scanner_gate,
        scanner_artifacts,
        report_path,
    )


def run_pilot(
    source: Path,
    output: Path,
    config: PilotRunConfig,
    scanner_gate: ScannerQualityGateResult,
    scanner_artifacts: ScannerArtifactGateResult,
    pilot_gate_path: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    audit = audit_staged_pilot(
        source,
        minimum_sessions=config.minimum_sessions,
        session_range=config.session_range,
    )
    write_pilot_audit(output, audit)
    if not audit.passed:
        write_alpaca_pilot_gate(
            pilot_gate_path,
            evaluate_alpaca_pilot_gate(
                audit,
                scanner_gate,
                orb_executed=False,
                scanner_artifacts=scanner_artifacts,
            ),
        )
        rprint(f"[red]파일럿 게이트 FAIL[/red] {len(audit.issues)}개 문제, {output}")
        raise typer.Exit(code=2)
    scanner_preflight = evaluate_alpaca_pilot_gate(
        audit,
        scanner_gate,
        orb_executed=False,
        scanner_artifacts=scanner_artifacts,
    )
    if (
        not scanner_gate.passed
        or not scanner_preflight.scanner_thresholds_sufficient
        or not scanner_preflight.scanner_artifacts_passed
    ):
        write_alpaca_pilot_gate(
            pilot_gate_path,
            scanner_preflight,
        )
        rprint(f"[red]스캐너 운영 게이트 FAIL[/red] ORB 미실행, {pilot_gate_path}")
        raise typer.Exit(code=2)
    grid = default_orb_grid()
    outcomes = analyze_alpaca_orb_grid(
        source,
        grid,
        AlpacaOrbArchiveConfig(config.assumed_spread_bps, config.max_positions),
        session_range=config.session_range,
    )
    write_orb_report(output, outcomes, grid)
    (output / "orb_forward_report_ko.md").unlink()
    artifact_gate = audit_orb_report_artifacts(
        output,
        expected_config_count=len(grid),
    )
    pilot_gate = evaluate_alpaca_pilot_gate(
        audit,
        scanner_gate,
        orb_executed=True,
        orb_outcome_count=len(outcomes),
        orb_config_count=len(grid),
        orb_artifacts=artifact_gate,
        scanner_artifacts=scanner_artifacts,
    )
    write_alpaca_pilot_gate(pilot_gate_path, pilot_gate)
    if not pilot_gate.passed:
        rprint(f"[red]ORB 구현 게이트 FAIL[/red] {len(pilot_gate.issues)}개 문제, {pilot_gate_path}")
        raise typer.Exit(code=2)
    _write_pilot_report(output, audit, len(outcomes), config)
    rprint(f"[green]파일럿 PASS[/green] {audit.session_count}세션, " + f"ORB outcome {len(outcomes)}건, {output}")


def _write_pilot_report(
    output: Path,
    audit: PilotAuditResult,
    outcome_count: int,
    config: PilotRunConfig,
) -> None:
    lines = (
        "# Alpaca 3개월 파일럿 ORB 보고서",
        "",
        "> 수익성 확정이 아니라 3년 확장 전 데이터·구현 게이트입니다.",
        "",
        f"- 완료 세션: {audit.session_count}",
        f"- 고정 기간: {audit.session_start or '전체'} ~ {audit.session_end or '전체'}",
        f"- ORB 파라미터·종목 outcome: {outcome_count}",
        f"- 최대 동시 포지션: {config.max_positions}",
        "- 진입·청산 편도 비용: 5/10/20bp",
        f"- quote 부재 위험 필터용 가정 spread: {config.assumed_spread_bps:g}bp",
        "- 기간분리: 2025년 이전 / 2025년 이후",
        "- 평탄성: 한 축만 한 단계 다른 인접 설정의 양수 지속성",
        "",
        "## 인과성과 체결",
        "",
        "- 스캐너는 09:30 ET 이전 봉만 사용합니다.",
        "- 각 역사 1분봉은 봉 종료 뒤에만 관찰된 것으로 처리합니다.",
        "- 돌파 신호 다음 1분부터 조건부 진입을 허용합니다.",
        "- 같은 봉에서 손절과 목표가 함께 닿으면 손절을 먼저 적용합니다.",
        "",
        "## 한계",
        "",
        "- 역사 NBBO·개별 halt/LULD·PIT float가 없어 실행 가능성을 확정하지 않습니다.",
        "- 현재 active+inactive 종목 목록은 완전한 PIT 종목마스터가 아닙니다.",
        "- 파일럿 PF를 보고 3년 확장 여부를 선택하지 않습니다.",
    )
    _ = (output / "alpaca_orb_pilot_report_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    typer.run(main)
