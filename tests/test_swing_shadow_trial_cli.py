from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import run_swing_shadow_trial as trial_cli
from tests.test_swing_shadow_trial import _bounds, _seed_signal, _session_source
from trading_agent.swing_shadow_engine import advance_swing_shadow_session

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_swing_shadow_trial.py"
REPORT_NAME = "swing_shadow_trial_ko.md"
UV_PATH = shutil.which("uv")
if UV_PATH is None:
    raise RuntimeError("uv is required for CLI tests")
UV = Path(UV_PATH)


def test_help_exposes_only_local_shadow_operations() -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )

    assert completed.returncode == 0
    for operation in ("register", "start", "finalize", "review"):
        assert operation in completed.stdout
    for forbidden in ("--credential", "--endpoint", "--arm", "--force", "--code-version"):
        assert forbidden not in completed.stdout


def test_unknown_option_creates_no_source_or_report(tmp_path: Path) -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "register", *_base_arguments(tmp_path), "--unknown-option"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )

    assert completed.returncode == 2
    assert not (tmp_path / "experiments.sqlite3").exists()
    assert not (tmp_path / "swing-shadow.sqlite3").exists()
    assert not (tmp_path / "report").exists()


def test_missing_register_source_is_blocked_without_creating_ledgers(tmp_path: Path) -> None:
    code = trial_cli.main(
        ("register", *_base_arguments(tmp_path)),
        now=dt.datetime(2026, 7, 20, 13, tzinfo=dt.UTC),
        runtime_code_version="test_code_v1",
    )

    assert code == 1
    assert not (tmp_path / "experiments.sqlite3").exists()
    assert not (tmp_path / "swing-shadow.sqlite3").exists()
    report = _report(tmp_path)
    assert "result: blocked_source" in report
    assert "external broker mutation: 0" in report
    assert stat.S_IMODE((tmp_path / "report" / REPORT_NAME).stat().st_mode) == 0o600
    _assert_redacted(report, tmp_path)


def test_fixture_register_start_finalize_review_and_replay_are_local_only(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    open_at, _ = _bounds(signal.valid_until.astimezone(dt.UTC).date())
    register_at = open_at - dt.timedelta(minutes=1)
    start_at = open_at + dt.timedelta(minutes=1)
    base = _base_arguments(tmp_path, signal_id=signal.signal_id)

    registered = trial_cli.main(
        ("register", *base),
        now=register_at,
        runtime_code_version="test_code_v1",
    )
    register_report = _report(tmp_path)
    register_replay = trial_cli.main(
        ("register", *base),
        now=start_at,
        runtime_code_version="test_code_v1",
    )
    register_replay_report = _report(tmp_path)
    started = trial_cli.main(("start", *base), now=start_at)
    start_report = _report(tmp_path)

    terminal_source = _session_source(
        signal.valid_until.astimezone(dt.UTC).date(),
        open_price=Decimal("14.80"),
        high=Decimal("15"),
        low=Decimal("14.50"),
        close=Decimal("14.90"),
    )
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=terminal_source)
    terminal = shadow.events(signal.signal_id)[-1]
    finalized = trial_cli.main(("finalize", *base), now=terminal.observed_at + dt.timedelta(minutes=1))
    finalize_report = _report(tmp_path)
    reviewed = trial_cli.main(
        ("review", *base, "--review-ledger", str(tmp_path / "review.sqlite3")),
        now=terminal.observed_at + dt.timedelta(minutes=2),
    )
    review_report = _report(tmp_path)
    review_replay = trial_cli.main(
        ("review", *base, "--review-ledger", str(tmp_path / "review.sqlite3")),
        now=terminal.observed_at + dt.timedelta(hours=1),
    )

    assert (registered, register_replay, started, finalized, reviewed, review_replay) == (0, 0, 0, 0, 0, 0)
    assert "operation: register" in register_report
    assert "created: true" in register_report
    assert "created: false" in register_replay_report
    assert "event_kind: started" in start_report
    assert "event_kind: completed" in finalize_report
    assert "reviewer_action: continue_collection" in review_report
    assert len(experiments.trials()) == 1
    assert stat.S_IMODE((tmp_path / "experiments.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "swing-shadow.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "review.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{tmp_path / 'experiments.sqlite3'}.writer.lock").stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{tmp_path / 'swing-shadow.sqlite3'}.writer.lock").stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{tmp_path / 'review.sqlite3'}.writer.lock").stat().st_mode) == 0o600
    for report in (register_report, start_report, finalize_report, review_report):
        _assert_redacted(report, tmp_path)


def test_cli_import_closure_excludes_operational_modules() -> None:
    script = """
import json
import sys
import run_swing_shadow_trial
print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))
"""
    completed = subprocess.run(
        (str(UV), "run", "python", "-c", script),
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )
    loaded_modules = json.loads(completed.stdout)
    forbidden = (
        "alpaca",
        "paper",
        "broker",
        "execution",
        "credential",
        "provider",
        "lifecycle_controller",
        "portfolio_manager",
    )

    assert not {
        module for module in loaded_modules if any(marker in module for marker in forbidden)
    }


def test_current_code_version_uses_an_identifier_safe_dirty_suffix(monkeypatch) -> None:
    responses = iter(
        (
            SimpleNamespace(stdout=f"{'a' * 40}\n"),
            SimpleNamespace(stdout=" M run_swing_shadow_trial.py\n"),
        )
    )
    monkeypatch.setattr(trial_cli.subprocess, "run", lambda *args, **kwargs: next(responses))

    assert trial_cli._current_code_version() == f"{'a' * 40}.dirty"


def _base_arguments(tmp_path: Path, *, signal_id: str = "missing") -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiments.sqlite3"),
        "--shadow-ledger",
        str(tmp_path / "swing-shadow.sqlite3"),
        "--signal-id",
        signal_id,
        "--output-dir",
        str(tmp_path / "report"),
    )


def _report(tmp_path: Path) -> str:
    return (tmp_path / "report" / REPORT_NAME).read_text(encoding="utf-8")


def _assert_redacted(report: str, tmp_path: Path) -> None:
    forbidden = (
        str(tmp_path),
        "swing-new-high-rvol-",
        "swing-shadow-",
        "account",
        "broker_order",
        "APCA_API",
        "https://",
    )
    assert all(value not in report for value in forbidden)


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
