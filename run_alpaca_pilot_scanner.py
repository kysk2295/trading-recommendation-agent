#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "rich>=13.9", "typer>=0.15"]
# ///
# How to run:
# ./run_alpaca_pilot_scanner.py <staged-root> --output-dir <result-dir>

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from trading_agent.alpaca_pilot_audit import audit_staged_pilot
from trading_agent.alpaca_pilot_audit_report import write_pilot_audit
from trading_agent.alpaca_scanner_quality import analyze_alpaca_scanner_quality
from trading_agent.alpaca_scanner_quality_gate import (
    ScannerQualityGateConfig,
    evaluate_scanner_quality_gate,
    write_scanner_quality_gate,
)
from trading_agent.alpaca_scanner_quality_models import scanner_quality_grid
from trading_agent.alpaca_scanner_quality_report import write_scanner_quality_report
from trading_agent.scanner_artifact_gate import audit_scanner_report_artifacts
from trading_agent.session_date_range import SessionDateRange


def main(
    source_dir: str,
    output_dir: str | None = None,
    minimum_sessions: int = 50,
    minimum_path_coverage: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.8,
    minimum_complete_candidate_days: Annotated[int, typer.Option(min=0)] = 100,
    start: Annotated[dt.date | None, typer.Option(parser=dt.date.fromisoformat)] = None,
    end: Annotated[dt.date | None, typer.Option(parser=dt.date.fromisoformat)] = None,
) -> None:
    source = Path(source_dir)
    if not source.is_dir():
        raise typer.BadParameter(f"Alpaca 단계형 아카이브를 찾을 수 없습니다: {source}")
    output = source / "pilot_scanner_quality" if output_dir is None else Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        session_range = SessionDateRange.optional(start, end)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    audit = audit_staged_pilot(
        source,
        minimum_sessions=minimum_sessions,
        session_range=session_range,
    )
    write_pilot_audit(output, audit)
    if not audit.passed:
        rprint(f"[red]파일럿 게이트 FAIL[/red] {len(audit.issues)}개 문제, {output}")
        raise typer.Exit(code=2)
    configs = scanner_quality_grid()
    outcomes = analyze_alpaca_scanner_quality(
        source,
        configs,
        session_range=session_range,
    )
    write_scanner_quality_report(output, outcomes, configs)
    artifacts = audit_scanner_report_artifacts(
        output,
        expected_config_count=len(configs),
    )
    gate = evaluate_scanner_quality_gate(
        outcomes,
        ScannerQualityGateConfig(
            minimum_path_coverage=minimum_path_coverage,
            minimum_complete_candidate_days=minimum_complete_candidate_days,
        ),
    )
    if not artifacts.passed:
        gate = replace(
            gate,
            passed=False,
            issues=(*gate.issues, *(f"artifact:{issue}" for issue in artifacts.issues)),
        )
    write_scanner_quality_gate(output, gate)
    if not gate.passed:
        rprint(
            f"[red]스캐너 데이터 게이트 FAIL[/red] {gate.unique_candidate_days} 후보-일, "
            + f"완전 {gate.complete_candidate_days}, 경로 {gate.path_coverage:.2%}, {output}"
        )
        raise typer.Exit(code=2)
    rprint(
        f"[green]스캐너 품질 PASS[/green] {audit.session_count}세션, "
        + f"고유 후보-일 {gate.unique_candidate_days}건, "
        + f"완전 {gate.complete_candidate_days}건, 경로 {gate.path_coverage:.2%}, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
