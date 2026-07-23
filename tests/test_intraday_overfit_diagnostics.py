from __future__ import annotations

import datetime as dt
import json
import math
import stat
import tomllib
from pathlib import Path

import pytest

import run_intraday_overfit_diagnostics as diagnostics_cli
import run_intraday_research_loop as research_cli
from trading_agent.intraday_overfit_diagnostics import (
    load_intraday_overfit_diagnostics_artifact,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    IntradayOverfitCandidateTrace,
    IntradayOverfitDiagnosticsStatus,
    IntradayOverfitStatistics,
    calculate_intraday_overfit_statistics,
)
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_overfit_diagnostics.py"
MANIFEST = PROJECT / "examples/research/intraday-challenger-bundle-v1.json"
INPUT = PROJECT / "examples/example_intraday.csv"
REVIEWED_AT = "2026-07-22T12:00:10+00:00"


def _candidate(
    strategy: str,
    returns: tuple[float, ...],
) -> IntradayOverfitCandidateTrace:
    experiment_hash = {"alpha": "a", "beta": "b", "gamma": "c"}[strategy]
    review_hash = {"alpha": "d", "beta": "e", "gamma": "f"}[strategy]
    return IntradayOverfitCandidateTrace(
        trial_id=f"trial-{strategy}",
        strategy_version=f"{strategy}-v2",
        experiment_artifact_id=experiment_hash * 64,
        review_artifact_id=review_hash * 64,
        trade_count=30,
        session_dates=tuple(
            dt.date(2026, 1, day)
            for day in range(1, len(returns) + 1)
        ),
        net_session_returns=returns,
    )


def _mature_candidates() -> tuple[IntradayOverfitCandidateTrace, ...]:
    variation = (-0.004, -0.002, 0.0, 0.002, 0.004)
    alpha = tuple(0.001 + delta for _ in range(4) for delta in variation)
    beta = tuple(0.002 + delta for _ in range(4) for delta in variation)
    gamma = tuple(0.003 + delta for _ in range(4) for delta in variation)
    return tuple(
        sorted(
            (
                _candidate("alpha", alpha),
                _candidate("beta", beta),
                _candidate("gamma", gamma),
            ),
            key=lambda item: item.strategy_version,
        )
    )


def _research_sources(
    tmp_path: Path,
) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]]:
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
        str(tmp_path / "diagnostics"),
        "--reviewed-at",
        REVIEWED_AT,
        "--output-dir",
        str(tmp_path / "diagnostics-report"),
    ]
    for path in experiments:
        values.extend(("--experiment-artifact", str(path)))
    for path in reviews:
        values.extend(("--review-artifact", str(path)))
    return tuple(values)


def test_dsr_and_cscv_pbo_are_computed_from_mature_synchronous_traces() -> None:
    candidates = _mature_candidates()

    statistics = calculate_intraday_overfit_statistics(
        candidates,
        total_lane_historical_trials=7,
    )

    assert statistics.status is IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY
    assert statistics.blockers == ()
    assert statistics.selected_strategy_version == "gamma-v2"
    assert statistics.cscv_partitions == 4
    assert len(statistics.cscv_logits) == 6
    assert statistics.pbo_probability == 0.0
    assert statistics.expected_max_sharpe is not None
    assert statistics.deflated_sharpe_probability is not None
    assert math.isclose(
        statistics.deflated_sharpe_probability,
        0.9949874120895517,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def test_diagnostics_are_collecting_without_twenty_v2_synchronous_sessions() -> None:
    candidates = tuple(
        _candidate(strategy, (0.01,))
        for strategy in ("alpha", "beta", "gamma")
    )

    statistics = calculate_intraday_overfit_statistics(
        candidates,
        total_lane_historical_trials=3,
    )

    assert statistics.status is IntradayOverfitDiagnosticsStatus.COLLECTING
    assert statistics.blockers == ("minimum_synchronous_sessions:1/20",)
    assert statistics.selected_strategy_version is None
    assert statistics.deflated_sharpe_probability is None
    assert statistics.pbo_probability is None


def test_statistics_reject_tampered_dsr_and_pbo_values() -> None:
    statistics = calculate_intraday_overfit_statistics(
        _mature_candidates(),
        total_lane_historical_trials=7,
    )
    tampered = statistics.model_dump(mode="json")
    tampered["deflated_sharpe_probability"] = 0.0
    tampered["pbo_probability"] = 1.0

    with pytest.raises(ValueError, match="invalid intraday overfit statistics"):
        _ = IntradayOverfitStatistics.model_validate(tampered)


def test_diagnostics_cli_declares_standalone_dependencies() -> None:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    opening = lines.index("# /// script")
    closing = lines.index("# ///", opening + 1)
    metadata = tomllib.loads(
        "\n".join(
            line.removeprefix("# ")
            for line in lines[opening + 1 : closing]
        )
    )

    assert "pydantic>=2.11" in metadata["dependencies"]


def test_diagnostics_cli_materializes_collecting_artifact_and_replays(
    tmp_path: Path,
) -> None:
    ledger, experiments, reviews = _research_sources(tmp_path)
    arguments = _arguments(ledger, experiments, reviews, tmp_path)

    first = diagnostics_cli.main(arguments)
    replay = diagnostics_cli.main(arguments)

    assert first == 0
    assert replay == 0
    paths = tuple(
        (tmp_path / "diagnostics").glob(
            "intraday_overfit_diagnostics_*.json"
        )
    )
    assert len(paths) == 1
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    artifact = json.loads(paths[0].read_text(encoding="utf-8"))
    payload = artifact["payload"]
    loaded = load_intraday_overfit_diagnostics_artifact(paths[0])
    assert loaded.artifact_id == artifact["artifact_id"]
    assert payload["statistics"]["status"] == "collecting"
    blockers = payload["statistics"]["blockers"]
    assert "minimum_synchronous_sessions:1/20" in blockers
    assert len(
        tuple(
            blocker
            for blocker in blockers
            if blocker.startswith("minimum_comparison_trades:")
        )
    ) == 3
    assert payload["automatic_state_change_allowed"] is False
    assert payload["order_authority_change_allowed"] is False
    assert payload["allocation_change_allowed"] is False
    report = (
        tmp_path
        / "diagnostics-report/intraday_overfit_diagnostics_ko.md"
    ).read_text(encoding="utf-8")
    assert "- result: collecting" in report
    assert "- candidate variants: 3" in report
    assert "- conservative lane trial count: 3" in report
    assert "- diagnostics artifact created: no" in report
    assert "- external mutation: 0" in report


def test_diagnostics_cli_blocks_mismatched_reviews_without_artifact(
    tmp_path: Path,
) -> None:
    ledger, experiments, reviews = _research_sources(tmp_path)

    result = diagnostics_cli.main(
        _arguments(
            ledger,
            experiments,
            (reviews[0], reviews[0], reviews[2]),
            tmp_path,
        )
    )

    assert result == 1
    assert not tuple((tmp_path / "diagnostics").glob("*.json"))
    report = (
        tmp_path
        / "diagnostics-report/intraday_overfit_diagnostics_ko.md"
    ).read_text(encoding="utf-8")
    assert "- result: blocked" in report
    assert "- external mutation: 0" in report
