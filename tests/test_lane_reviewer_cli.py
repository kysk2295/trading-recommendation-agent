from __future__ import annotations

import ast
import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import run_lane_reviewer as reviewer_cli
from tests.test_lane_reviewer import (
    REVIEWED_AT,
    SESSION_DATE,
    _adaptive_path,
    _ReviewSources,
    _sources,
)

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_lane_reviewer.py"
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)


def test_reviewer_help_is_executable_and_local_only() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert "--session-date" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "--review-ledger" in completed.stdout
    assert "credential" not in completed.stdout.lower()
    assert "arm" not in completed.stdout.lower()


def test_reviewer_module_has_no_broker_or_execution_imports() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        name
        for node in ast.walk(tree)
        for name in (
            *((alias.name for alias in node.names) if isinstance(node, ast.Import) else ()),
            *((node.module,) if isinstance(node, ast.ImportFrom) and node.module else ()),
        )
    }

    assert not any(
        forbidden in name
        for name in imported
        for forbidden in (
            "alpaca",
            "execution_store",
            "paper_runtime",
            "mutation",
            "httpx",
        )
    )


def test_reviewer_invalid_date_is_argparse_error() -> None:
    completed = subprocess.run(
        (
            str(SCRIPT),
            "missing-session",
            "--session-date",
            "not-a-date",
            "--lane-registry",
            "missing-registry",
            "--review-ledger",
            "missing-review",
            "--output-dir",
            "missing-output",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert "YYYY-MM-DD" in completed.stderr


def test_missing_snapshot_writes_generic_blocked_report(tmp_path: Path) -> None:
    sources = _sources(tmp_path, snapshot=False)
    output = tmp_path / "missing-snapshot-report"

    code = reviewer_cli.main(
        _args(
            sources.session,
            sources.registry.path,
            sources.reviews.path,
            output,
        ),
        clock=lambda: REVIEWED_AT,
    )

    assert code == 1
    assert sources.reviews.events() == ()
    report = _report(output)
    assert "결과: blocked" in report
    assert "review append: not_written" in report
    _assert_redacted(report, sources)


def test_missing_local_path_does_not_create_review_ledger(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    missing_session = tmp_path / "missing-session"
    review_path = tmp_path / "never-created-review.sqlite3"
    output = tmp_path / "missing-path-report"

    code = reviewer_cli.main(
        _args(missing_session, sources.registry.path, review_path, output),
        clock=lambda: REVIEWED_AT,
    )

    assert code == 1
    assert not review_path.exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert str(missing_session) not in report


def test_reviewer_cli_creates_then_replays_redacted_event(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    first_output = tmp_path / "first-report"
    replay_output = tmp_path / "replay-report"

    first = reviewer_cli.main(
        _args(
            sources.session,
            sources.registry.path,
            sources.reviews.path,
            first_output,
        ),
        clock=lambda: REVIEWED_AT,
    )
    replay = reviewer_cli.main(
        _args(
            sources.session,
            sources.registry.path,
            sources.reviews.path,
            replay_output,
        ),
        clock=lambda: REVIEWED_AT + dt.timedelta(minutes=5),
    )

    assert first == 0
    assert replay == 0
    assert len(sources.reviews.events()) == 1
    first_report = _report(first_output)
    replay_report = _report(replay_output)
    assert "결과: reviewed" in first_report
    assert "review append: created" in first_report
    assert "review append: replayed" in replay_report
    assert "adaptive action: collecting" in first_report
    assert "Reviewer action: continue_collection" in first_report
    assert "champion_missing" in first_report
    assert "자동 상태 변경: 금지" in first_report
    assert "주문 권한 변경: 금지" in first_report
    assert "외부 Alpaca mutation: 0건" in first_report
    _assert_redacted(first_report, sources)
    _assert_redacted(replay_report, sources)


def test_reviewer_cli_reports_tampered_adaptive_as_immutable_conflict(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    assert (
        reviewer_cli.main(
            _args(
                sources.session,
                sources.registry.path,
                sources.reviews.path,
                tmp_path / "first",
            ),
            clock=lambda: REVIEWED_AT,
        )
        == 0
    )
    adaptive = _adaptive_path(sources.session)
    adaptive.write_text(
        adaptive.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "conflict-report"

    code = reviewer_cli.main(
        _args(
            sources.session,
            sources.registry.path,
            sources.reviews.path,
            output,
        ),
        clock=lambda: REVIEWED_AT + dt.timedelta(minutes=5),
    )

    assert code == 1
    assert len(sources.reviews.events()) == 1
    report = _report(output)
    assert "결과: conflict" in report
    assert "review append: conflict" in report
    assert "adaptive_evaluation_sha256" not in report
    _assert_redacted(report, sources)


def _args(
    session: Path,
    registry: Path,
    reviews: Path,
    output: Path,
) -> list[str]:
    return [
        str(session),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--lane-registry",
        str(registry),
        "--review-ledger",
        str(reviews),
        "--output-dir",
        str(output),
    ]


def _report(output: Path) -> str:
    return (output / reviewer_cli.REPORT_NAME).read_text(encoding="utf-8")


def _assert_redacted(report: str, sources: _ReviewSources) -> None:
    for forbidden in (
        str(sources.session),
        str(sources.registry.path),
        str(sources.reviews.path),
        "snapshot_key",
        "scope_key",
        "record_id",
        "sha256",
        "account_fingerprint",
        "broker_order_id",
        "payload_json",
    ):
        assert forbidden not in report


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
