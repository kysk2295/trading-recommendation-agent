#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_us_day_paper_smoke_eligibility.py --help
# 3. Or make executable and run:
#      chmod +x run_us_day_paper_smoke_eligibility.py
#      ./run_us_day_paper_smoke_eligibility.py --help
# ──────────────────

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.hermes_arm_request import HermesArmScope
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_smoke_eligibility import (
    PaperSmokeEligibility,
    PaperSmokeEligibilityConfig,
    audit_paper_smoke_eligibility,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "paper_smoke_eligibility_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US Day Paper smoke의 local-only champion/control-plane 자격 감사"
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--execution-database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--lane-id",
        choices=tuple(item.value for item in LaneId),
        default=LaneId.INTRADAY_MOMENTUM.value,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    scope = HermesArmScope(
        session_id=args.session_id,
        lane_id=LaneId(args.lane_id),
    )
    result = audit_paper_smoke_eligibility(
        PaperSmokeEligibilityConfig(
            repository=args.repository,
            lane_registry=args.lane_registry,
            experiment_ledger=args.experiment_ledger,
            execution_database=args.execution_database,
        ),
        scope,
    )
    _write_report(args.output_dir, scope, result)
    return 0 if result.ready_to_request_arm else 1


def _write_report(
    output_dir: Path,
    scope: HermesArmScope,
    result: PaperSmokeEligibility,
) -> None:
    status = "ready_to_request_arm" if result.ready_to_request_arm else "blocked"
    lines = [
        "# US Day Paper smoke eligibility",
        "",
        "> 주문 승인이나 arm 생성이 아닌 local-only 사전 자격 감사입니다.",
        "",
        f"- result: {status}",
        f"- session: {scope.session_id}",
        f"- lane: {scope.lane_id.value}",
        *(f"- blocker: {blocker}" for blocker in result.blockers),
        "- explicit arm still required: yes",
        "- external provider/account/order mutation: 0",
        "",
    ]
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
