from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.intraday_actual_research_prerequisite import (
    CloseoutPrerequisiteRequest,
    CloseoutPrerequisiteRuntime,
    require_closeout_prerequisite,
)
from trading_agent.private_report import write_private_report


def test_planned_actual_research_prerequisite_requires_both_paths(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "closeout.receipt"

    with pytest.raises(ValueError, match="prerequisite_paths_incomplete"):
        require_closeout_prerequisite(
            CloseoutPrerequisiteRequest(
                receipt=receipt,
                report=None,
            )
        )


def test_planned_actual_research_waits_for_closeout_published_before_deadline(
    tmp_path: Path,
) -> None:
    # Given
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    now = dt.datetime(2026, 7, 24, 20, 18, tzinfo=dt.UTC)
    waits: list[float] = []

    def publish_closeout(delay: float) -> None:
        waits.append(delay)
        write_private_report(
            receipt,
            "exit_code=0\ncompleted_at_epoch=1784924281\n",
        )
        write_private_report(report, _closeout_report("recovered"))

    # When
    require_closeout_prerequisite(
        CloseoutPrerequisiteRequest(
            receipt=receipt,
            report=report,
            wait_until=now + dt.timedelta(seconds=5),
        ),
        runtime=CloseoutPrerequisiteRuntime(
            clock=lambda: now,
            sleeper=publish_closeout,
        ),
    )

    # Then
    assert waits == [1.0]


def test_planned_actual_research_stops_waiting_at_closeout_deadline(
    tmp_path: Path,
) -> None:
    # Given
    now = dt.datetime(2026, 7, 24, 20, 20, tzinfo=dt.UTC)
    request = CloseoutPrerequisiteRequest(
        receipt=tmp_path / "missing.receipt",
        report=tmp_path / "missing-report.md",
        wait_until=now,
    )
    runtime = CloseoutPrerequisiteRuntime(
        clock=lambda: now,
        sleeper=lambda _delay: pytest.fail("slept after deadline"),
    )

    # When / Then
    with pytest.raises(ValueError, match="closeout_prerequisite_timeout"):
        require_closeout_prerequisite(request, runtime=runtime)


@pytest.mark.parametrize(
    ("receipt_payload", "report_result"),
    (
        ("exit_code=1\ncompleted_at_epoch=1784862960\n", "recovered"),
        ("exit_code=0\ncompleted_at_epoch=1784862960\n", "blocked"),
    ),
)
def test_planned_actual_research_prerequisite_blocks_failed_closeout(
    tmp_path: Path,
    receipt_payload: str,
    report_result: str,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(receipt, receipt_payload)
    write_private_report(report, _closeout_report(report_result))

    with pytest.raises(ValueError, match="closeout_prerequisite_invalid"):
        require_closeout_prerequisite(
            CloseoutPrerequisiteRequest(
                receipt=receipt,
                report=report,
            )
        )


@pytest.mark.parametrize("result", ("recovered", "replayed"))
def test_planned_actual_research_prerequisite_accepts_strict_closeout(
    tmp_path: Path,
    result: str,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784862960\n",
    )
    write_private_report(report, _closeout_report(result))

    require_closeout_prerequisite(
        CloseoutPrerequisiteRequest(
            receipt=receipt,
            report=report,
        )
    )


@pytest.mark.parametrize(
    ("minimum_watch_cycles", "ranking_cycles"),
    ((1, 300), (300, 299)),
)
def test_planned_actual_research_prerequisite_rejects_relaxed_closeout(
    tmp_path: Path,
    minimum_watch_cycles: int,
    ranking_cycles: int,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784862960\n",
    )
    write_private_report(
        report,
        _closeout_report(
            "recovered",
            minimum_watch_cycles=minimum_watch_cycles,
            ranking_cycles=ranking_cycles,
        ),
    )

    with pytest.raises(ValueError, match="closeout_prerequisite_invalid"):
        require_closeout_prerequisite(
            CloseoutPrerequisiteRequest(
                receipt=receipt,
                report=report,
            )
        )


def _closeout_report(
    result: str,
    *,
    minimum_watch_cycles: int = 300,
    ranking_cycles: int = 300,
) -> str:
    return (
        "# Forward post-session strict closeout\n\n"
        f"- result: {result}\n"
        f"- minimum watch cycles: {minimum_watch_cycles}\n"
        "- watch cycles: 300\n"
        f"- ranking cycles: {ranking_cycles}\n"
        "- retry cycles: 300\n"
        "- candidate input cycles: 300\n"
        "- failed cycle deletion: 0\n"
        "- quality gate relaxed: false\n"
        "- provider, credential, account, or order operation: 0\n"
    )
