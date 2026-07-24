#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly:
#      uv run run_intraday_actual_research_audit.py --help
# 3. Or make executable and run:
#      chmod +x run_intraday_actual_research_audit.py
#      ./run_intraday_actual_research_audit.py --help
# ──────────────────

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.intraday_actual_research_audit import (
    audit_intraday_actual_research,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditError,
    IntradayActualResearchAuditRequest,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "intraday_actual_research_audit_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit exact actual intraday research terminal evidence")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--research-receipt", type=Path, required=True)
    parser.add_argument("--research-report", type=Path, required=True)
    parser.add_argument("--expected-dataset-producer-commit-sha", required=True)
    parser.add_argument("--expected-code-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = args.output_dir.resolve(strict=False)
    try:
        result = audit_intraday_actual_research(
            IntradayActualResearchAuditRequest(
                run_key=args.run_key,
                plan_path=args.plan.resolve(strict=False),
                research_receipt=args.research_receipt.resolve(strict=False),
                research_report=args.research_report.resolve(strict=False),
                expected_dataset_producer_commit_sha=(args.expected_dataset_producer_commit_sha),
                expected_code_version=args.expected_code_version,
                output_root=output_root,
            )
        )
    except (IntradayActualResearchAuditError, ValidationError):
        write_private_report(
            output_root / REPORT_NAME,
            "# Intraday actual research terminal audit\n\n"
            "- result: blocked\n"
            "- automatic state change: false\n"
            "- order authority change: false\n"
            "- allocation change: false\n"
            "- external mutation: 0\n",
        )
        return 1
    payload = result.artifact.payload
    decisions = ", ".join(item.value for item in payload.reviewer_decisions)
    comparison = "not_applicable" if payload.comparison_status is None else payload.comparison_status.value
    diagnostics = (
        "not_applicable"
        if payload.overfit_diagnostics_status is None
        else payload.overfit_diagnostics_status.value
    )
    plateau = (
        "not_applicable"
        if payload.parameter_plateau_status is None
        else payload.parameter_plateau_status.value
    )
    write_private_report(
        output_root / REPORT_NAME,
        "# Intraday actual research terminal audit\n\n"
        "- result: ready\n"
        + f"- run key: {payload.run_key}\n"
        + f"- plan id: {payload.plan_id}\n"
        + f"- dataset input sha256: {payload.dataset_input_sha256}\n"
        + f"- dataset producer commit: {payload.dataset_producer_commit_sha}\n"
        + f"- strategy code version: {payload.strategy_code_version}\n"
        + f"- manifest sha256: {payload.manifest_sha256}\n"
        + f"- READY foundations: {len(payload.foundation_sha256s)}\n"
        + f"- completed trials: {len(payload.trial_ids)}\n"
        + f"- independent reviews: {len(payload.review_artifact_ids)}\n"
        + f"- reviewer decisions: {decisions}\n"
        + f"- equal-risk comparison: {comparison}\n"
        + f"- DSR/PBO diagnostics: {diagnostics}\n"
        + f"- parameter plateau: {plateau}\n"
        + "- automatic state change: false\n"
        + "- order authority change: false\n"
        + "- allocation change: false\n"
        + "- external mutation: 0\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
