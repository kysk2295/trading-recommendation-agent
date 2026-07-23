from __future__ import annotations

import csv
import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.challenger_replay_fixtures import write_closed_source_session
from trading_agent.intraday_research_dataset import materialize_intraday_research_dataset
from trading_agent.intraday_research_dataset_catalog import (
    materialize_intraday_research_dataset_catalog,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogError,
    IntradayResearchDatasetCatalogRequest,
)
from trading_agent.intraday_research_dataset_models import (
    IntradayResearchDatasetError,
    IntradayResearchDatasetRequest,
    IntradayResearchDatasetResult,
)
from trading_agent.replay import load_bounded_bar_source

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_research_dataset.py"
CATALOG_SCRIPT = PROJECT / "run_intraday_research_dataset_catalog.py"


def test_materializer_publishes_causal_multi_session_dataset(tmp_path: Path) -> None:
    # Given: two complete point-in-time source sessions with one censored symbol each.
    first = tmp_path / "2026-07-14"
    second = tmp_path / "2026-07-15"
    write_closed_source_session(first)
    write_closed_source_session(second, session_date=dt.date(2026, 7, 15))

    # When: the sessions are materialized for bounded historical research.
    result = materialize_intraday_research_dataset(
        IntradayResearchDatasetRequest(
            session_dirs=(first, second),
            output_root=tmp_path / "datasets",
            max_sessions=2,
            max_bars=1_000,
        )
    )
    replay = load_bounded_bar_source(result.csv_path, max_rows=1_000, max_sessions=2)

    # Then: only bars that start after candidate observation enter the immutable dataset.
    assert result.created is True
    assert result.session_count == 2
    assert result.eligible_symbol_sessions == 2
    assert result.censored_symbol_sessions == 2
    assert result.bar_count == 768
    assert replay.sha256 == result.input_sha256
    assert replay.bars[0].timestamp.isoformat() == "2026-07-14T09:36:00-04:00"
    assert replay.bars[-1].timestamp.isoformat() == "2026-07-15T15:59:00-04:00"
    assert {row.symbol for row in replay.bars} == {"DEMO"}
    assert stat.S_IMODE(result.csv_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.receipt_path.stat().st_mode) == 0o600
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["input_sha256"] == result.input_sha256
    assert receipt["session_dates"] == ["2026-07-14", "2026-07-15"]
    assert receipt["source_session_sha256s"] == list(result.source_session_sha256s)


def test_materializer_exact_replay_does_not_replace_artifacts(tmp_path: Path) -> None:
    # Given: one complete source session.
    source = tmp_path / "source"
    write_closed_source_session(source, include_censored_symbol=False)
    request = IntradayResearchDatasetRequest(
        session_dirs=(source,),
        output_root=tmp_path / "datasets",
        max_sessions=1,
        max_bars=500,
    )

    # When: the exact source is materialized twice.
    first = materialize_intraday_research_dataset(request)
    replay = materialize_intraday_research_dataset(request)

    # Then: content-addressed files are reused without mutation.
    assert first.created is True
    assert replay.created is False
    assert replay == IntradayResearchDatasetResult(
        csv_path=first.csv_path,
        receipt_path=first.receipt_path,
        input_sha256=first.input_sha256,
        source_session_sha256s=first.source_session_sha256s,
        session_count=first.session_count,
        eligible_symbol_sessions=first.eligible_symbol_sessions,
        censored_symbol_sessions=first.censored_symbol_sessions,
        bar_count=first.bar_count,
        created=False,
    )


def test_materializer_rejects_any_ineligible_session_before_publish(tmp_path: Path) -> None:
    # Given: one complete session and one session missing its post-close proof.
    complete = tmp_path / "complete"
    incomplete = tmp_path / "incomplete"
    write_closed_source_session(complete)
    write_closed_source_session(
        incomplete,
        post_session_complete=False,
        session_date=dt.date(2026, 7, 15),
    )

    # When/Then: the whole requested dataset fails closed without partial publication.
    with pytest.raises(IntradayResearchDatasetError):
        _ = materialize_intraday_research_dataset(
            IntradayResearchDatasetRequest(
                session_dirs=(complete, incomplete),
                output_root=tmp_path / "datasets",
                max_sessions=2,
                max_bars=1_000,
            )
        )
    assert not (tmp_path / "datasets").exists()


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
                required_session_dates=(dt.date(2026, 7, 15),),
            )
        )
    assert not (tmp_path / "catalog").exists()


def test_dataset_cli_exposes_sessions_and_runs_happy_path(tmp_path: Path) -> None:
    # Given: the operator CLI and one complete source session.
    source = tmp_path / "source"
    write_closed_source_session(source, include_censored_symbol=False)

    # When: help and a real local materialization are invoked.
    help_result = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--session-dir",
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
            "--max-sessions",
            "1",
            "--max-bars",
            "500",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the user-facing surface reports a ready immutable research input.
    assert help_result.returncode == 0
    assert "--session-dir" in help_result.stdout
    assert "--max-sessions" in help_result.stdout
    assert "--max-bars" in help_result.stdout
    assert completed.returncode == 0, completed.stderr
    report = (tmp_path / "output" / "intraday_research_dataset_ko.md").read_text(encoding="utf-8")
    assert "- result: ready" in report
    assert "- sessions: 1" in report
    assert "- external mutation: 0" in report
    csv_paths = tuple((tmp_path / "output").glob("intraday_point_in_time_*.csv"))
    assert len(csv_paths) == 1
    with csv_paths[0].open(encoding="utf-8", newline="") as handle:
        assert tuple(csv.DictReader(handle))


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
