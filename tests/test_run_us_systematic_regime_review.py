from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import run_us_systematic_regime_review as cli
from tests.test_systematic_regime_reviewer import _completed_trial
from trading_agent.swing_shadow_cli_files import write_private_swing_source
from trading_agent.systematic_regime_review_store import SystematicRegimeReviewReader


def test_cli_reviews_all_terminal_trials_and_replays_exactly(tmp_path: Path) -> None:
    experiment, systematic, card, source = _completed_trial(tmp_path)
    source_root = tmp_path / "source-root"
    _ = write_private_swing_source(source_root, source)
    reviews = tmp_path / "reviews.sqlite3"
    output = tmp_path / "output"
    args = [
        "--experiment-ledger",
        str(experiment.path),
        "--systematic-database",
        str(systematic.path),
        "--source-root",
        str(source_root),
        "--review-ledger",
        str(reviews),
        "--output-dir",
        str(output),
        "--all-terminal",
    ]

    first = cli.main(args, now=source.observed_at + dt.timedelta(minutes=1))
    replay = cli.main(args, now=source.observed_at + dt.timedelta(hours=1))

    assert first == 0
    assert replay == 0
    events = SystematicRegimeReviewReader(reviews).events()
    assert len(events) == 1
    assert events[0].event.card_id == card.card_id
    report = (output / "us_systematic_regime_review_ko.md").read_text(encoding="utf-8")
    assert "result: completed" in report
    assert "eligible_trials: 1" in report
    assert "reviews_created: 0" in report
    assert "reviews_replayed: 1" in report
    assert "external broker mutation: 0" in report
    assert stat.S_IMODE((output / "us_systematic_regime_review_ko.md").stat().st_mode) == 0o600


def test_cli_blocks_explicit_review_when_exact_source_is_missing(tmp_path: Path) -> None:
    experiment, systematic, card, source = _completed_trial(tmp_path)
    reviews = tmp_path / "reviews.sqlite3"
    output = tmp_path / "output"

    result = cli.main(
        [
            "--experiment-ledger",
            str(experiment.path),
            "--systematic-database",
            str(systematic.path),
            "--source-root",
            str(tmp_path / "missing-source-root"),
            "--review-ledger",
            str(reviews),
            "--output-dir",
            str(output),
            "--card-id",
            card.card_id,
        ],
        now=source.observed_at + dt.timedelta(minutes=1),
    )

    assert result == 1
    assert not reviews.exists()
    report = (output / "us_systematic_regime_review_ko.md").read_text(encoding="utf-8")
    assert "result: blocked_source" in report
    assert "external broker mutation: 0" in report


def test_cli_blocks_missing_source_ledgers_instead_of_reporting_empty_success(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"

    result = cli.main(
        [
            "--experiment-ledger",
            str(tmp_path / "missing-experiment.sqlite3"),
            "--systematic-database",
            str(tmp_path / "missing-systematic.sqlite3"),
            "--source-root",
            str(tmp_path / "source-root"),
            "--review-ledger",
            str(tmp_path / "reviews.sqlite3"),
            "--output-dir",
            str(output),
            "--all-terminal",
        ],
        now=dt.datetime(2026, 7, 24, 20, 10, tzinfo=dt.UTC),
    )

    assert result == 1
    assert not (tmp_path / "reviews.sqlite3").exists()
    assert "result: blocked_source" in (
        output / "us_systematic_regime_review_ko.md"
    ).read_text(encoding="utf-8")
