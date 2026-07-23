from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.challenger_replay_fixtures import write_closed_source_session
from trading_agent.intraday_research_dataset_catalog import (
    materialize_intraday_research_dataset_catalog,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogError,
    IntradayResearchDatasetCatalogRequest,
)

PROJECT = Path(__file__).resolve().parents[1]
CATALOG_SCRIPT = PROJECT / "run_intraday_research_dataset_catalog.py"
PRODUCER_COMMIT = "a" * 40


def test_catalog_audits_each_session_and_materializes_only_strict_eligible_sources(
    tmp_path: Path,
) -> None:
    # Given: two strict sessions and one independently quality-blocked session.
    first = tmp_path / "2026-07-14"
    blocked = tmp_path / "2026-07-15"
    second = tmp_path / "2026-07-16"
    write_closed_source_session(first, session_date=dt.date(2026, 7, 14))
    write_closed_source_session(
        blocked,
        post_session_complete=False,
        session_date=dt.date(2026, 7, 15),
    )
    write_closed_source_session(second, session_date=dt.date(2026, 7, 16))

    # When: the catalog applies the existing replay gate before accumulation.
    result = materialize_intraday_research_dataset_catalog(
        IntradayResearchDatasetCatalogRequest(
            session_dirs=(first, blocked, second),
            output_root=tmp_path / "catalog",
            minimum_sessions=2,
            max_sessions=3,
            max_bars=1_000,
            producer_commit_sha=PRODUCER_COMMIT,
        )
    )
    receipt = json.loads(result.catalog_receipt_path.read_text(encoding="utf-8"))

    # Then: exclusion is explicit and the dataset contains only both strict sessions.
    assert result.dataset.session_count == 2
    assert result.dataset.bar_count == 768
    assert receipt["selected_session_dates"] == ["2026-07-14", "2026-07-16"]
    assert [row["session_name"] for row in receipt["audits"]] == [
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
    ]
    assert receipt["audits"][1]["eligible"] is False
    assert "post_session_metrics_missing_or_failed" in receipt["audits"][1]["reason_codes"]
    assert stat.S_IMODE(result.catalog_receipt_path.stat().st_mode) == 0o600


def test_catalog_blocks_before_publication_when_clean_session_floor_is_not_met(
    tmp_path: Path,
) -> None:
    # Given: one strict session and one blocked session against a two-session floor.
    complete = tmp_path / "2026-07-14"
    blocked = tmp_path / "2026-07-15"
    write_closed_source_session(complete, session_date=dt.date(2026, 7, 14))
    write_closed_source_session(
        blocked,
        post_session_complete=False,
        session_date=dt.date(2026, 7, 15),
    )

    # When/Then: no partial dataset or catalog receipt is published.
    with pytest.raises(IntradayResearchDatasetCatalogError):
        _ = materialize_intraday_research_dataset_catalog(
            IntradayResearchDatasetCatalogRequest(
                session_dirs=(complete, blocked),
                output_root=tmp_path / "catalog",
                minimum_sessions=2,
                max_sessions=2,
                max_bars=1_000,
                producer_commit_sha=PRODUCER_COMMIT,
            )
        )
    assert not (tmp_path / "catalog").exists()


def test_catalog_exact_replay_reuses_dataset_and_audit_receipt(tmp_path: Path) -> None:
    source = tmp_path / "2026-07-14"
    write_closed_source_session(source, session_date=dt.date(2026, 7, 14))
    request = IntradayResearchDatasetCatalogRequest(
        session_dirs=(source,),
        output_root=tmp_path / "catalog",
        minimum_sessions=1,
        max_sessions=1,
        max_bars=500,
        producer_commit_sha=PRODUCER_COMMIT,
    )

    first = materialize_intraday_research_dataset_catalog(request)
    replay = materialize_intraday_research_dataset_catalog(request)

    assert first.created is True
    assert replay.created is False
    assert replay.catalog_receipt_path == first.catalog_receipt_path
    assert replay.catalog_receipt_sha256 == first.catalog_receipt_sha256
    assert len(tuple(request.output_root.glob("intraday_research_catalog_*.json"))) == 1


def test_catalog_requires_the_current_session_to_be_strict_eligible(tmp_path: Path) -> None:
    complete = tmp_path / "2026-07-14"
    blocked = tmp_path / "2026-07-15"
    write_closed_source_session(complete, session_date=dt.date(2026, 7, 14))
    write_closed_source_session(
        blocked,
        post_session_complete=False,
        session_date=dt.date(2026, 7, 15),
    )

    with pytest.raises(IntradayResearchDatasetCatalogError):
        _ = materialize_intraday_research_dataset_catalog(
            IntradayResearchDatasetCatalogRequest(
                session_dirs=(complete, blocked),
                output_root=tmp_path / "catalog",
                minimum_sessions=1,
                max_sessions=2,
                max_bars=1_000,
                producer_commit_sha=PRODUCER_COMMIT,
                required_session_dates=(dt.date(2026, 7, 15),),
            )
        )
    assert not (tmp_path / "catalog").exists()


def test_catalog_cli_reports_selected_and_blocked_session_counts(tmp_path: Path) -> None:
    # Given: one strict and one blocked source session.
    complete = tmp_path / "2026-07-14"
    blocked = tmp_path / "2026-07-15"
    write_closed_source_session(complete, session_date=dt.date(2026, 7, 14))
    write_closed_source_session(
        blocked,
        post_session_complete=False,
        session_date=dt.date(2026, 7, 15),
    )

    # When: an operator runs the cumulative catalog CLI.
    help_result = subprocess.run(
        (sys.executable, str(CATALOG_SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        (
            sys.executable,
            str(CATALOG_SCRIPT),
            "--session-dir",
            str(complete),
            "--session-dir",
            str(blocked),
            "--output-dir",
            str(tmp_path / "catalog"),
            "--producer-commit-sha",
            PRODUCER_COMMIT,
            "--minimum-sessions",
            "1",
            "--max-sessions",
            "2",
            "--max-bars",
            "500",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the redacted report distinguishes selection from quality exclusion.
    assert help_result.returncode == 0
    assert "--minimum-sessions" in help_result.stdout
    assert completed.returncode == 0, completed.stderr
    report = (tmp_path / "catalog" / "intraday_research_dataset_catalog_ko.md").read_text(
        encoding="utf-8"
    )
    assert "- result: ready" in report
    assert "- candidate sessions: 2" in report
    assert "- selected sessions: 1" in report
    assert "- blocked sessions: 1" in report
    assert "- external mutation: 0" in report
