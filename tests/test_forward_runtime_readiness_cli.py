from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import run_forward_runtime_readiness
from trading_agent.execution_store import ExecutionStore
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
)
from trading_agent.lane_registry_store import LaneRegistryStore

SESSION_DATE = dt.date(2026, 7, 27)
BOOTSTRAPPED_AT = dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _runtime(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "runtime"
    repo.mkdir(mode=0o700)
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "readiness@example.invalid")
    _git(repo, "config", "user.name", "Readiness Test")
    (repo / "runtime.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "runtime.txt")
    _git(repo, "commit", "--quiet", "-m", "base")
    required = _git(repo, "rev-parse", "HEAD")
    (repo / "runtime.txt").write_text("ready\n", encoding="utf-8")
    _git(repo, "add", "runtime.txt")
    _git(repo, "commit", "--quiet", "-m", "ready")
    head = _git(repo, "rev-parse", "HEAD")
    repo.chmod(0o700)
    return repo, required, head


def _stores(tmp_path: Path, *, code_version: str) -> tuple[Path, Path, Path]:
    lane_path = tmp_path / "lane.sqlite3"
    lane_store = LaneRegistryStore(lane_path)
    with lane_store.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(manifest)
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            _ = writer.register_experiment_scope(scope)

    experiment_path = tmp_path / "experiment.sqlite3"
    _ = bootstrap_current_intraday_experiments(
        lane_registry=lane_store,
        experiment_ledger=ExperimentLedgerStore(experiment_path),
        code_version=code_version,
        recorded_at=BOOTSTRAPPED_AT,
    )

    execution_path = tmp_path / "execution.sqlite3"
    with ExecutionStore(execution_path).writer():
        pass
    return lane_path, experiment_path, execution_path


def _arguments(
    tmp_path: Path,
    *,
    runtime: Path,
    head: str,
    required: str,
    stores: tuple[Path, Path, Path],
) -> list[str]:
    lane, experiment, execution = stores
    return [
        "--runtime-dir",
        str(runtime),
        "--expected-head",
        head,
        "--required-commit",
        required,
        "--session-date",
        SESSION_DATE.isoformat(),
        "--experiment-ledger",
        str(experiment),
        "--lane-registry",
        str(lane),
        "--execution-database",
        str(execution),
        "--cycles",
        "390",
        "--interval-seconds",
        "60",
        "--kis-server-attempts",
        "4",
        "--eod-last-bar-semantic-attempts",
        "3",
        "--output-dir",
        str(tmp_path / "report"),
    ]


def test_readiness_is_ready_for_exact_frozen_runtime_and_active_ledgers(tmp_path: Path) -> None:
    runtime, required, head = _runtime(tmp_path)
    stores = _stores(tmp_path, code_version=head)

    exit_code = run_forward_runtime_readiness.main(
        _arguments(tmp_path, runtime=runtime, head=head, required=required, stores=stores)
    )

    report = tmp_path / "report" / "forward_runtime_readiness_ko.md"
    assert exit_code == 0
    assert report.stat().st_mode & 0o777 == 0o600
    assert "- 결과: ready" in report.read_text(encoding="utf-8")


def test_readiness_blocks_runtime_head_without_active_version(tmp_path: Path) -> None:
    runtime, required, head = _runtime(tmp_path)
    stores = _stores(tmp_path, code_version=required)

    exit_code = run_forward_runtime_readiness.main(
        _arguments(tmp_path, runtime=runtime, head=head, required=required, stores=stores)
    )

    report = (tmp_path / "report" / "forward_runtime_readiness_ko.md").read_text(encoding="utf-8")
    assert exit_code == 1
    assert "- 결과: blocked" in report
    assert "runtime_version_not_active" in report
    assert str(runtime) not in report


def test_readiness_blocks_when_required_recovery_commit_is_missing(tmp_path: Path) -> None:
    runtime, _required, head = _runtime(tmp_path)
    stores = _stores(tmp_path, code_version=head)
    unrelated = "f" * 40

    exit_code = run_forward_runtime_readiness.main(
        _arguments(tmp_path, runtime=runtime, head=head, required=unrelated, stores=stores)
    )

    report = (tmp_path / "report" / "forward_runtime_readiness_ko.md").read_text(encoding="utf-8")
    assert exit_code == 1
    assert "required_commit_missing" in report
    assert unrelated not in report


def test_readiness_blocks_non_strict_runtime_configuration(tmp_path: Path) -> None:
    runtime, required, head = _runtime(tmp_path)
    stores = _stores(tmp_path, code_version=head)
    arguments = _arguments(tmp_path, runtime=runtime, head=head, required=required, stores=stores)
    arguments[arguments.index("390")] = "389"

    exit_code = run_forward_runtime_readiness.main(arguments)

    report = (tmp_path / "report" / "forward_runtime_readiness_ko.md").read_text(encoding="utf-8")
    assert exit_code == 1
    assert "runtime_config_mismatch" in report


def test_readiness_rejects_noncanonical_commit_before_writing_report(tmp_path: Path) -> None:
    runtime, required, head = _runtime(tmp_path)
    stores = _stores(tmp_path, code_version=head)
    arguments = _arguments(tmp_path, runtime=runtime, head=head, required=required, stores=stores)
    arguments[arguments.index(head)] = "short-sha"

    try:
        run_forward_runtime_readiness.main(arguments)
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("argparse must reject a noncanonical commit")
    assert not (tmp_path / "report").exists()
