#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.data_foundation_manifest import (
    InvalidDataFoundationManifestError,
    load_data_foundation_manifest,
)
from trading_agent.private_report import write_private_report
from trading_agent.strategy_data_gate import (
    DataRequirementStatus,
    InvalidStrategyDataEvaluationError,
    StrategyDataStatus,
)

REPORT_NAME = "data_foundation_check_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="local-only data foundation 계약과 전략 data gate를 검증")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = load_data_foundation_manifest(args.manifest)
        decision = manifest.evaluate_data_readiness()
    except (
        InvalidDataFoundationManifestError,
        InvalidStrategyDataEvaluationError,
        ValidationError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            details=(
                "contract validation: failed",
                "network access: 0",
                "broker mutation: 0",
            ),
        )
        return 1

    satisfied = sum(
        evaluation.status is DataRequirementStatus.SATISFIED
        for evaluation in decision.evaluations
    )
    fallbacks = sum(evaluation.fallback_used for evaluation in decision.evaluations)
    _write_report(
        args.output_dir,
        result=decision.status.value,
        details=(
            "contract validation: passed",
            f"requirement 충족/전체: {satisfied}/{len(decision.evaluations)}",
            f"declared source: {len(manifest.capabilities)}",
            f"instrument/event: {len(manifest.instruments)}/{len(manifest.events)}",
            f"fallback selected: {fallbacks}",
            "network access: 0",
            "broker mutation: 0",
        ),
    )
    return 0 if decision.status is StrategyDataStatus.READY else 2


def _write_report(
    output_dir: Path,
    *,
    result: str,
    details: tuple[str, ...],
) -> None:
    lines = (
        "# Data foundation contract check",
        "",
        "> provider 연결, 자격증명 로딩, 주문 또는 실행 mutation 없는 local-only 계약 평가입니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "",
    )
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
