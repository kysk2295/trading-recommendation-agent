from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_models_v2 import SourceStateV2
from trading_agent.dashboard_options_workbench_models import PromotionSummaryV2
from trading_agent.intraday_promotion_models import PromotionAssessmentStatus
from trading_agent.intraday_promotion_store import (
    InvalidIntradayPromotionArtifactError,
    load_promotion_approval,
    load_promotion_assessment,
)


def promotion_summaries(
    outputs: Path,
    now: dt.datetime,
    strategies: SourceStateV2 | None,
) -> tuple[PromotionSummaryV2, ...]:
    root = outputs / "promotion_control"
    assessments = ()
    approvals = ()
    if root.exists():
        try:
            metadata = root.lstat()
            if (
                root.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise InvalidIntradayPromotionArtifactError
            assessment_paths = tuple(sorted(root.glob("intraday_promotion_assessment_*.json")))
            approval_paths = tuple(sorted(root.glob("intraday_promotion_approval_*.json")))
            if len(assessment_paths) > 100 or len(approval_paths) > 100:
                raise InvalidIntradayPromotionArtifactError
            assessments = tuple(load_promotion_assessment(path) for path in assessment_paths)
            approvals = tuple(load_promotion_approval(path) for path in approval_paths)
        except (InvalidIntradayPromotionArtifactError, OSError):
            return ()
    approved = {
        approval.content.assessment_id for approval in approvals if approval.content.approved_at <= now
    }
    summaries: list[PromotionSummaryV2] = []
    assessed_versions: set[str] = set()
    for assessment in sorted(
        assessments,
        key=lambda item: item.content.assessed_at,
        reverse=True,
    )[:20]:
        if assessment.content.assessed_at > now:
            continue
        assessed_versions.add(assessment.content.strategy_version)
        trace_id = _strategy_trace_id(strategies, assessment.content.strategy_version)
        if assessment.assessment_id in approved:
            state: Literal["held", "approved"] = "approved"
            blockers: tuple[str, ...] = ()
            passed = 7
        else:
            state = "held"
            blockers = (
                ("manual_approval_required",)
                if assessment.content.status is PromotionAssessmentStatus.ELIGIBLE
                else assessment.content.blockers
            )
            passed = (
                6
                if assessment.content.status is PromotionAssessmentStatus.MANUAL_APPROVAL_PENDING
                else max(0, 6 - len(blockers))
            )
        summaries.append(
            PromotionSummaryV2(
                promotion_id=assessment.assessment_id,
                state=state,
                passed_gate_count=passed,
                total_gate_count=7,
                blockers=blockers,
                trace_id=trace_id,
            )
        )
    if strategies is not None:
        for item in strategies.items:
            if len(summaries) >= 20 or item.kind != "strategy" or item.value is None:
                continue
            strategy_version = item.value.partition(" ·")[0]
            if strategy_version in assessed_versions:
                continue
            summaries.append(
                PromotionSummaryV2(
                    promotion_id=hashlib.sha256(
                        f"unassessed:{item.item_id}:{item.trace_id}".encode()
                    ).hexdigest(),
                    state="held",
                    passed_gate_count=0,
                    total_gate_count=7,
                    blockers=("promotion_assessment_missing",),
                    trace_id=item.trace_id,
                )
            )
    return tuple(summaries)


def _strategy_trace_id(strategies: SourceStateV2 | None, strategy_version: str) -> str:
    if strategies is None:
        return "trace.strategies.ledger"
    for item in strategies.items:
        if item.value is not None and item.value.startswith(f"{strategy_version} ·"):
            return item.trace_id
    return strategies.trace_id


__all__ = ("promotion_summaries",)
