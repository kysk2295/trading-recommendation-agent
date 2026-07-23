from __future__ import annotations

import json
import stat
import tomllib
from pathlib import Path

import run_intraday_equal_risk_comparison as comparison_cli
import run_intraday_research_loop as research_cli
from trading_agent.intraday_equal_risk_comparison_models import (
    EqualRiskComparisonCandidate,
    EqualRiskComparisonStatus,
    equal_risk_comparison_status,
)
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_equal_risk_comparison.py"
MANIFEST = PROJECT / "examples/research/intraday-challenger-bundle-v1.json"
INPUT = PROJECT / "examples/example_intraday.csv"
REVIEWED_AT = "2026-07-22T12:00:10+00:00"


def _candidate(
    strategy: str,
    *,
    observed_sessions: int,
    trade_count: int,
) -> EqualRiskComparisonCandidate:
    return EqualRiskComparisonCandidate(
        trial_id=f"trial-{strategy}",
        strategy_version=f"{strategy}-v1",
        experiment_artifact_id="a" * 64,
        review_artifact_id="b" * 64,
        observed_sessions=observed_sessions,
        trade_count=trade_count,
        reviewer_decision=IntradayReviewerDecision.HOLD,
    )


def _research_sources(tmp_path: Path) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]]:
    lanes = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lanes))
    ledger = tmp_path / "experiment.sqlite3"
    artifacts = tmp_path / "experiments"
    reviews = tmp_path / "reviews"
    result = research_cli.main(
        (
            "--manifest",
            str(MANIFEST),
            "--input-csv",
            str(INPUT),
            "--lane-registry",
            str(lanes),
            "--experiment-ledger",
            str(ledger),
            "--artifact-root",
            str(artifacts),
            "--review-root",
            str(reviews),
            "--output-dir",
            str(tmp_path / "research-report"),
        )
    )
    assert result == 0
    return (
        ledger,
        tuple(sorted(artifacts.glob("intraday_walk_forward_*.json"))),
        tuple(sorted(reviews.glob("intraday_research_review_*.json"))),
    )


def _arguments(
    ledger: Path,
    experiments: tuple[Path, ...],
    reviews: tuple[Path, ...],
    tmp_path: Path,
) -> tuple[str, ...]:
    values = [
        "--experiment-ledger",
        str(ledger),
        "--artifact-root",
        str(tmp_path / "comparisons"),
        "--reviewed-at",
        REVIEWED_AT,
        "--output-dir",
        str(tmp_path / "comparison-report"),
    ]
    for path in experiments:
        values.extend(("--experiment-artifact", str(path)))
    for path in reviews:
        values.extend(("--review-artifact", str(path)))
    return tuple(values)


def test_comparison_policy_requires_two_mature_equal_risk_candidates() -> None:
    collecting = (
        _candidate("vwap", observed_sessions=20, trade_count=30),
        _candidate("hod", observed_sessions=19, trade_count=30),
    )
    ready = (
        _candidate("vwap", observed_sessions=20, trade_count=30),
        _candidate("hod", observed_sessions=20, trade_count=30),
    )

    assert equal_risk_comparison_status(collecting) is EqualRiskComparisonStatus.COLLECTING
    assert equal_risk_comparison_status(ready) is EqualRiskComparisonStatus.COMPARISON_READY


def test_comparison_cli_declares_standalone_dependencies() -> None:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    opening = lines.index("# /// script")
    closing = lines.index("# ///", opening + 1)
    metadata = tomllib.loads("\n".join(line.removeprefix("# ") for line in lines[opening + 1 : closing]))

    assert "pydantic>=2.11" in metadata["dependencies"]


def test_comparison_cli_materializes_exact_collecting_artifact_and_replays(
    tmp_path: Path,
) -> None:
    ledger, experiments, reviews = _research_sources(tmp_path)
    arguments = _arguments(ledger, experiments, reviews, tmp_path)

    first = comparison_cli.main(arguments)
    replay = comparison_cli.main(arguments)

    assert first == 0
    assert replay == 0
    paths = tuple((tmp_path / "comparisons").glob("intraday_equal_risk_comparison_*.json"))
    assert len(paths) == 1
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    artifact = json.loads(paths[0].read_text(encoding="utf-8"))
    assert artifact["payload"]["status"] == "collecting"
    assert len(artifact["payload"]["candidates"]) == 3
    assert artifact["payload"]["automatic_state_change_allowed"] is False
    assert artifact["payload"]["order_authority_change_allowed"] is False
    assert artifact["payload"]["allocation_change_allowed"] is False
    report = (tmp_path / "comparison-report/intraday_equal_risk_comparison_ko.md").read_text(encoding="utf-8")
    assert "- result: collecting" in report
    assert "- candidates: 3" in report
    assert "- comparison artifact created: no" in report
    assert "- external mutation: 0" in report


def test_comparison_cli_blocks_mismatched_trial_review_set_without_artifact(
    tmp_path: Path,
) -> None:
    ledger, experiments, reviews = _research_sources(tmp_path)

    result = comparison_cli.main(
        _arguments(
            ledger,
            experiments[:2],
            (reviews[0], reviews[0]),
            tmp_path,
        )
    )

    assert result == 1
    assert not tuple((tmp_path / "comparisons").glob("*.json"))
    report = (tmp_path / "comparison-report/intraday_equal_risk_comparison_ko.md").read_text(encoding="utf-8")
    assert "- result: blocked" in report
    assert "- external mutation: 0" in report
