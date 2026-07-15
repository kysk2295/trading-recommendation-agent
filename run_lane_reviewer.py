#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_agent.lane_registry_store import LaneRegistryReader
from trading_agent.lane_review_store import LaneReviewConflictError, LaneReviewStore
from trading_agent.lane_reviewer import review_intraday_lane_day

REPORT_NAME = "lane_reviewer_ko.md"
BLOCKED_REASON = "finalized snapshot과 exact 연구 계보를 확인하지 못했습니다"
CONFLICT_REASON = "같은 immutable review identity의 근거가 변경되었습니다"
SAFE_BLOCKERS = frozenset(
    {
        "allocation_ineligible",
        "automatic_promotion_forbidden",
        "broker_paper_ledger_missing",
        "champion_missing",
        "data_quality_incomplete",
        "dsr_pbo_missing",
        "parameter_plateau_missing",
        "sip_validation_missing",
    }
)
SAFE_BLOCKER_PREFIXES = (
    "cohort_instability:",
    "gap_feature_coverage_",
    "minimum_completed_trades:",
    "minimum_forward_days:",
    "regime_",
    "rolling_",
    "trade_feature_coverage_",
)


@dataclass(frozen=True, slots=True)
class LaneReviewerReport:
    result: str
    session_date: dt.date
    append_state: str
    adaptive_action: str | None
    reviewer_action: str | None
    blockers: tuple[str, ...]


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="finalized ORB lane snapshot을 로컬 query-only 근거로 독립 검토")
    parser.add_argument("session", type=Path)
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    try:
        result = review_intraday_lane_day(
            LaneRegistryReader(args.lane_registry),
            LaneReviewStore(args.review_ledger),
            args.session,
            args.session_date,
            reviewed_at=clock(),
        )
    except LaneReviewConflictError:
        report = LaneReviewerReport(
            result="conflict",
            session_date=args.session_date,
            append_state="conflict",
            adaptive_action=None,
            reviewer_action=None,
            blockers=(),
        )
        return 1 if _write_report(args.output_dir, report) else 2
    except (OSError, RuntimeError, UnicodeError, ValueError, sqlite3.Error):
        report = LaneReviewerReport(
            result="blocked",
            session_date=args.session_date,
            append_state="not_written",
            adaptive_action=None,
            reviewer_action=None,
            blockers=(),
        )
        return 1 if _write_report(args.output_dir, report) else 2

    event = result.event
    report = LaneReviewerReport(
        result="reviewed",
        session_date=event.session_date,
        append_state="created" if result.created else "replayed",
        adaptive_action=event.adaptive_action.value,
        reviewer_action=event.reviewer_action.value,
        blockers=tuple(blocker for blocker in event.blockers if _reportable_blocker(blocker)),
    )
    return 0 if _write_report(args.output_dir, report) else 2


def _reportable_blocker(blocker: str) -> bool:
    return blocker in SAFE_BLOCKERS or blocker.startswith(SAFE_BLOCKER_PREFIXES)


def _write_report(output_dir: Path, report: LaneReviewerReport) -> bool:
    adaptive_action = report.adaptive_action or "미평가"
    reviewer_action = report.reviewer_action or "미평가"
    blockers = report.blockers or ("보고 가능한 blocker 없음",)
    lines = [
        "# ORB lane 독립 Reviewer",
        "",
        "> 확정 수익, champion 선언 또는 주문 승인이 아닌 로컬 검토 권고입니다.",
        "",
        f"- 결과: {report.result}",
        "- lane: intraday_momentum",
        f"- 거래일: {report.session_date.isoformat()}",
        f"- review append: {report.append_state}",
        f"- adaptive action: {adaptive_action}",
        f"- Reviewer action: {reviewer_action}",
        "- 자동 상태 변경: 금지",
        "- 주문 권한 변경: 금지",
        "- 외부 Alpaca mutation: 0건",
        "- blockers:",
        *(f"  - {blocker}" for blocker in blockers),
    ]
    if report.result == "blocked":
        lines.append(f"- 차단 사유: {BLOCKED_REASON}")
    elif report.result == "conflict":
        lines.append(f"- 차단 사유: {CONFLICT_REASON}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / REPORT_NAME
        temporary = destination.with_suffix(".tmp")
        _ = temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
