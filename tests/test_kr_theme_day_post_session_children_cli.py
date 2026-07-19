from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import run_kr_theme_day_reviewer as reviewer_cli
import run_kr_theme_day_trial_terminal as terminal_cli
from tests.test_kr_theme_day_reviewer import REVIEWED_AT
from tests.test_kr_theme_day_shadow_entry import VERSION, _ledger
from tests.test_kr_theme_day_trial_terminal import CLOSED_AT, _trial_stores
from trading_agent.kr_theme_day_review_store import KrThemeDayReviewStore

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_SCRIPT = ROOT / "run_kr_theme_day_trial_terminal.py"
REVIEWER_SCRIPT = ROOT / "run_kr_theme_day_reviewer.py"
TERMINAL_REPORT = "kr_theme_day_trial_terminal_ko.md"
REVIEWER_REPORT = "kr_theme_day_reviewer_ko.md"


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    return (
        tmp_path / "experiment.sqlite3",
        tmp_path / "entries.sqlite3",
        tmp_path / "exits.sqlite3",
        tmp_path / "terminals.sqlite3",
        tmp_path / "reviews.sqlite3",
    )


def _terminal_args(tmp_path: Path, trial_id: str) -> tuple[str, ...]:
    experiment, entries, exits, terminals, _ = _paths(tmp_path)
    return (
        "--experiment-ledger",
        str(experiment),
        "--entry-store",
        str(entries),
        "--exit-store",
        str(exits),
        "--terminal-store",
        str(terminals),
        "--trial-id",
        trial_id,
        "--output-dir",
        str(tmp_path / "terminal-report"),
    )


def _reviewer_args(tmp_path: Path) -> tuple[str, ...]:
    experiment, entries, exits, terminals, reviews = _paths(tmp_path)
    return (
        "--experiment-ledger",
        str(experiment),
        "--entry-store",
        str(entries),
        "--exit-store",
        str(exits),
        "--terminal-store",
        str(terminals),
        "--review-store",
        str(reviews),
        "--strategy-version",
        VERSION,
        "--as-of-session",
        "2026-07-20",
        "--output-dir",
        str(tmp_path / "review-report"),
    )


def test_child_help_exposes_local_evidence_without_authority_options() -> None:
    for script in (TERMINAL_SCRIPT, REVIEWER_SCRIPT):
        completed = subprocess.run(
            (str(script), "--help"),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert "--experiment-ledger" in completed.stdout
        for forbidden in ("--account", "--arm", "--credential", "--endpoint", "--force", "--order"):
            assert forbidden not in completed.stdout.lower()


def test_terminal_cli_finalizes_and_exactly_replays_private_report(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    args = _terminal_args(tmp_path, trial_id)

    first = terminal_cli.main(args, occurred_at=CLOSED_AT)
    first_report = (tmp_path / "terminal-report" / TERMINAL_REPORT).read_text(encoding="utf-8")
    replay = terminal_cli.main(args, occurred_at=CLOSED_AT + dt.timedelta(minutes=1))
    replay_report = (tmp_path / "terminal-report" / TERMINAL_REPORT).read_text(encoding="utf-8")

    assert (first, replay) == (0, 0)
    assert "event_kind: completed" in first_report
    assert "artifact_created: true" in first_report
    assert "event_created: true" in first_report
    assert "artifact_created: false" in replay_report
    assert "event_created: false" in replay_report
    assert trial_id not in replay_report
    assert len(ledger.multi_market_trial_events(trial_id)) == 2
    assert len(stores.terminal_store.artifacts()) == 1
    assert stat.S_IMODE((tmp_path / "terminal-report" / TERMINAL_REPORT).stat().st_mode) == 0o600


def test_reviewer_cli_reviews_terminal_and_exactly_replays(tmp_path: Path) -> None:
    _, trial_id = _trial_stores(tmp_path)
    _ = _ledger(tmp_path / "experiment.sqlite3")
    assert terminal_cli.main(_terminal_args(tmp_path, trial_id), occurred_at=CLOSED_AT) == 0
    args = _reviewer_args(tmp_path)

    first = reviewer_cli.main(args, reviewed_at=REVIEWED_AT)
    first_report = (tmp_path / "review-report" / REVIEWER_REPORT).read_text(encoding="utf-8")
    replay = reviewer_cli.main(args, reviewed_at=REVIEWED_AT + dt.timedelta(minutes=1))
    replay_report = (tmp_path / "review-report" / REVIEWER_REPORT).read_text(encoding="utf-8")

    assert (first, replay) == (0, 0)
    assert "action: continue_collection" in first_report
    assert "completed_sessions: 1" in first_report
    assert "completed_trades: 1" in first_report
    assert "created: true" in first_report
    assert "created: false" in replay_report
    assert VERSION not in replay_report
    assert len(KrThemeDayReviewStore(tmp_path / "reviews.sqlite3").events()) == 1
    assert stat.S_IMODE((tmp_path / "review-report" / REVIEWER_REPORT).stat().st_mode) == 0o600


def test_missing_child_sources_block_without_creating_control_rows(tmp_path: Path) -> None:
    terminal = terminal_cli.main(_terminal_args(tmp_path, "missing"), occurred_at=CLOSED_AT)
    reviewer = reviewer_cli.main(_reviewer_args(tmp_path), reviewed_at=REVIEWED_AT)

    assert (terminal, reviewer) == (1, 1)
    assert not (tmp_path / "experiment.sqlite3").exists()
    assert not (tmp_path / "terminals.sqlite3").exists()
    assert not (tmp_path / "reviews.sqlite3").exists()


def test_child_import_closure_excludes_operational_authority() -> None:
    script = """
import json
import sys
import run_kr_theme_day_reviewer
import run_kr_theme_day_trial_terminal
print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))
"""
    completed = subprocess.run(
        (sys.executable, "-c", script),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    modules: list[str] = json.loads(completed.stdout)
    forbidden = ("alpaca", "broker", "credential", "execution", "paper_", "portfolio_manager", "provider")
    assert not {module for module in modules if any(marker in module for marker in forbidden)}
