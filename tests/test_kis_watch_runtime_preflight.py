from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import typer

import run_kis_paper_watch as watch
from trading_agent.operational_child_preflight import (
    preflight_operational_children,
)

PROJECT = Path(__file__).resolve().parents[1]
CORE_CHILDREN = {
    "run_adaptive_strategy_evaluation.py",
    "run_daily_research_record.py",
    "run_kis_eod_catchup.py",
    "run_kis_paper_scan.py",
    "run_paper_metrics.py",
}


def test_runtime_preflight_executes_operational_children_from_isolated_directory() -> None:
    # Given: the watch will invoke core children and one optional premarket child.
    calls: list[tuple[str, Path]] = []

    def run(script: Path, cwd: Path) -> int:
        calls.append((script.name, cwd))
        return 0

    # When: the standalone runtime preflight executes.
    failures = preflight_operational_children(
        PROJECT,
        ("run_kis_premarket_scan.py",),
        run,
    )

    # Then: every selected child imports outside the project environment.
    assert failures == ()
    assert {name for name, _cwd in calls} == {
        *CORE_CHILDREN,
        "run_kis_premarket_scan.py",
    }
    assert all(cwd != PROJECT for _name, cwd in calls)
    assert len({cwd for _name, cwd in calls}) == 1


def test_runtime_preflight_reports_failed_child_without_stderr_payload() -> None:
    # Given: only the adaptive child cannot start in its standalone environment.
    def run(script: Path, _cwd: Path) -> int:
        return int(script.name == "run_adaptive_strategy_evaluation.py")

    # When: the runtime preflight evaluates all core children.
    failures = preflight_operational_children(PROJECT, (), run)

    # Then: the caller receives only the child name, not raw process output.
    assert failures == ("run_adaptive_strategy_evaluation.py",)


def test_watch_blocks_failed_runtime_preflight_before_market_or_provider_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a selected standalone child cannot import.
    market_checked = False

    def mark_market_checked(_observed_at: dt.datetime) -> bool:
        nonlocal market_checked
        market_checked = True
        return False

    monkeypatch.setattr(
        watch,
        "preflight_operational_children",
        lambda _root, _optional: ("run_adaptive_strategy_evaluation.py",),
        raising=False,
    )
    monkeypatch.setattr(watch, "regular_session_is_open", mark_market_checked)

    # When: the operator starts a watch.
    with pytest.raises(typer.BadParameter, match="standalone runtime preflight"):
        watch.main(cycles=1)

    # Then: the watch rejects the launch before any market/provider decision.
    assert market_checked is False
