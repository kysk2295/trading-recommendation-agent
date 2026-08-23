#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run python run_kr_day_close_report.py --help
# ─────────────────

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.day_learning_policy import ExplorationPolicyAction
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_learning_policy import publish_kr_day_learning_policy
from trading_agent.kr_day_market_close_report import (
    KrDayMarketCloseRequest,
    publish_kr_day_market_close_report,
)
from trading_agent.private_immutable_file import read_private_text


class _CliResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Literal["published", "blocked"]
    report_id: str | None
    metrics_id: str | None
    policy_id: str | None
    revision: int | None
    effective_session_date: str | None
    modeled_return: float | None
    cumulative_modeled_return: float | None
    win_rate: float | None
    mean_r: float | None
    profit_factor: float | None
    max_drawdown: float | None
    failed_count: int | None
    censored_count: int | None
    risk_incident_count: int | None
    data_incident_count: int | None
    selection_diagnostic_count: int | None
    actual_return: None = None
    provider_read_only: Literal[True] = True
    profitability_claim: Literal[False] = False
    report_created: bool
    metrics_created: bool
    policy_created: bool


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize local KR Shadow research after market close.")
    parser.add_argument("--finalization", type=Path, required=True)
    parser.add_argument("--shadow-store", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--policy-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        paths = (args.finalization, args.shadow_store, args.report_root, args.policy_root)
        if any(not path.is_absolute() or path.is_symlink() for path in paths):
            raise ValueError
        raw = read_private_text(args.finalization)
        request = KrDayMarketCloseRequest.model_validate_json(raw)
        if raw != canonical_experiment_ledger_json(request) + "\n":
            raise ValueError
        stored_events = tuple(
            event
            for event in KrDayCapsuleShadowStore(args.shadow_store).events()
            if event.session_date == request.session_date
        )
        if stored_events != request.shadow_events:
            raise ValueError
        report = publish_kr_day_market_close_report(args.report_root, request)
        policy = publish_kr_day_learning_policy(
            args.report_root,
            args.policy_root,
            report.report,
            request.calendar_snapshot,
            ExplorationPolicyAction.KEEP,
        )
        result = _CliResult(
            result="published",
            report_id=report.report.report_id,
            metrics_id=report.metrics.metrics_id,
            policy_id=policy.policy.policy_id,
            revision=report.report.payload.revision,
            effective_session_date=policy.policy.payload.effective_session_date.isoformat(),
            modeled_return=report.report.payload.execution.modeled_return,
            cumulative_modeled_return=report.metrics.payload.cumulative_cost_adjusted_shadow_return,
            win_rate=report.metrics.payload.win_rate,
            mean_r=report.metrics.payload.mean_r,
            profit_factor=report.metrics.payload.profit_factor,
            max_drawdown=report.metrics.payload.cumulative_max_drawdown,
            failed_count=report.metrics.payload.failed_count,
            censored_count=report.metrics.payload.censored_count,
            risk_incident_count=len(report.metrics.payload.risk_incident_ids),
            data_incident_count=len(report.metrics.payload.data_incident_ids),
            selection_diagnostic_count=len(report.metrics.payload.selection_diagnostics),
            report_created=report.created,
            metrics_created=report.metrics_created,
            policy_created=policy.created,
        )
    except (OSError, TypeError, ValidationError, ValueError):
        result = _blocked_result()
        _emit(result)
        return 2
    _emit(result)
    return 0


def _blocked_result() -> _CliResult:
    return _CliResult(
        result="blocked",
        report_id=None,
        metrics_id=None,
        policy_id=None,
        revision=None,
        effective_session_date=None,
        modeled_return=None,
        cumulative_modeled_return=None,
        win_rate=None,
        mean_r=None,
        profit_factor=None,
        max_drawdown=None,
        failed_count=None,
        censored_count=None,
        risk_incident_count=None,
        data_incident_count=None,
        selection_diagnostic_count=None,
        report_created=False,
        metrics_created=False,
        policy_created=False,
    )


def _emit(result: _CliResult) -> None:
    sys.stdout.write(canonical_experiment_ledger_json(result) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
