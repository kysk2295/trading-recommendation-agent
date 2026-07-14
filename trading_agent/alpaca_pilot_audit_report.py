from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from trading_agent.alpaca_pilot_audit import PilotAuditResult


def write_pilot_audit(output: Path, audit: PilotAuditResult) -> None:
    _ = (output / "pilot_audit.json").write_text(
        json.dumps(dataclasses.asdict(audit), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = (
        "# Alpaca 파일럿 데이터 감사",
        "",
        f"- 판정: {'PASS' if audit.passed else 'FAIL'}",
        f"- 감사 기간: {audit.session_start or '전체'} ~ {audit.session_end or '전체'}",
        f"- 완료 세션: {audit.session_count}",
        f"- 선택 후보 합계: {audit.selected_symbol_count}",
        f"- 스캐너/후속 분봉: {audit.scanner_bar_count} / {audit.candidate_bar_count}",
        f"- 스캐너/후속 중복: {audit.scanner_duplicate_count} / {audit.candidate_duplicate_count}",
        f"- 시간 인과성 위반: {audit.temporal_violation_count}",
        f"- 미완료 파일: {audit.incomplete_artifact_count}",
        "",
        "## 문제",
        "",
        *(f"- {issue}" for issue in audit.issues),
    )
    _ = (output / "pilot_audit_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
