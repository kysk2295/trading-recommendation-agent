from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from tests.test_us_day_lifecycle import _terminal_thesis
from tests.test_us_day_signal_admission import _eligible_request
from trading_agent.dashboard_projection_day_agent import project_day_agent_facade
from trading_agent.dashboard_projection_day_agent_us import read_us_day_paper_events
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore
from trading_agent.us_day_lifecycle import InvalidUsDayLifecycleError
from trading_agent.us_day_thesis_models import DayTradeDecision
from trading_agent.us_day_thesis_store import UsDayThesisStore


def test_dashboard_projects_us_armed_active_and_terminal_from_same_history(tmp_path: Path) -> None:
    # Given: a persisted US thesis and append-only paper state transitions.
    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True, mode=0o700)
    thesis = _eligible_request().thesis
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(thesis)
    paper = PaperStore(outputs / "us_day" / "paper.sqlite3")
    paper.save(_recommendation(thesis.thesis_id))
    paper.set_state(thesis.thesis_id, RecommendationState.ACTIVE, thesis.observed_at, None, "entry_acknowledged")
    paper.set_state(thesis.thesis_id, RecommendationState.STOPPED, thesis.observed_at, 99.0, "stop_first")

    # When: the dashboard projects the current US lifecycle.
    projection = project_day_agent_facade(outputs, now=thesis.observed_at + dt.timedelta(seconds=4))

    # Then: the terminal and immutable timeline are visible with the original plan.
    items = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.us.lifecycle"))
    assert any("STOPPED" in item.label for item in items)
    rendered = " ".join(item.value or "" for item in items)
    assert all(value in rendered for value in ("Alpaca Paper", "entry", "stop", "targets", "stop_first"))
    assert len(tuple(node for node in projection.nodes if node.node_id.startswith("trace.us.lifecycle"))) >= 3
    snapshot = collect_dashboard_snapshot_v2(outputs, now=thesis.observed_at + dt.timedelta(seconds=4))
    assert thesis.symbol is not None
    current_labels = tuple(item.label for item in snapshot.workspaces.markets.items if thesis.symbol in item.label)
    assert current_labels and all("ACTIVE" not in label and "active thesis" not in label for label in current_labels)
    assert any("STOPPED" in label for label in current_labels)


def test_dashboard_projects_us_investigating_and_rejected_reasons(tmp_path: Path) -> None:
    # Given: two non-entry thesis decisions with exact reason codes.
    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True, mode=0o700)
    source = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    investigating = _terminal_thesis(
        source,
        decision=DayTradeDecision.WATCH,
        reason_code="price_setup_incomplete",
    )
    rejected = _terminal_thesis(
        source,
        decision=DayTradeDecision.NO_TRADE,
        reason_code="spread_too_wide",
    )
    assert store.publish_thesis(investigating) and store.publish_thesis(rejected)

    # When: the dashboard reads only the immutable thesis source.
    projection = project_day_agent_facade(outputs, now=source.observed_at + dt.timedelta(seconds=4))

    # Then: neither disposition is hidden behind a missing recommendation.
    rendered = " ".join(
        f"{item.label} {item.value}" for item in projection.markets if item.item_id.startswith("day_agent.us.lifecycle")
    )
    assert all(value in rendered for value in ("INVESTIGATING", "price_setup_incomplete"))
    assert all(value in rendered for value in ("REJECTED", "spread_too_wide"))


def test_query_only_paper_reader_does_not_create_or_mutate_files(tmp_path: Path) -> None:
    # Given: one existing paper database and one absent database path.
    paper_path = tmp_path / "paper.sqlite3"
    thesis = _eligible_request().thesis
    paper = PaperStore(paper_path)
    paper.save(_recommendation(thesis.thesis_id))
    before = paper_path.stat()
    missing = tmp_path / "missing.sqlite3"

    # When: dashboard readers inspect both paths.
    events = read_us_day_paper_events(paper_path, (thesis.thesis_id,))
    absent = read_us_day_paper_events(missing, (thesis.thesis_id,))
    after = paper_path.stat()

    # Then: rows are visible without file, schema, journal, or metadata mutation.
    assert tuple(item.state for item in events[thesis.thesis_id]) == (RecommendationState.SETUP,)
    assert absent == {}
    assert not missing.exists()
    assert (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) == (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    )
    assert not Path(f"{paper_path}-wal").exists()
    assert not Path(f"{paper_path}-shm").exists()


def test_query_only_paper_reader_rejects_naive_time_and_symlink(tmp_path: Path) -> None:
    # Given: malformed timestamp evidence and a symlink replacement.
    paper_path = tmp_path / "paper.sqlite3"
    thesis = _eligible_request().thesis
    paper = PaperStore(paper_path)
    paper.save(_recommendation(thesis.thesis_id))
    with sqlite3.connect(paper_path) as connection:
        _ = connection.execute("UPDATE events SET occurred_at = '2026-08-20T14:07:00'")
    linked = tmp_path / "linked.sqlite3"
    linked.symlink_to(paper_path)
    broken = tmp_path / "broken.sqlite3"
    broken.symlink_to(tmp_path / "absent-target.sqlite3")

    # When / Then: neither malformed source crosses the dashboard boundary.
    with pytest.raises(InvalidUsDayLifecycleError):
        _ = read_us_day_paper_events(paper_path, (thesis.thesis_id,))
    with pytest.raises(InvalidUsDayLifecycleError):
        _ = read_us_day_paper_events(linked, (thesis.thesis_id,))
    with pytest.raises(InvalidUsDayLifecycleError):
        _ = read_us_day_paper_events(broken, (thesis.thesis_id,))


def _recommendation(thesis_id: str) -> Recommendation:
    thesis = _eligible_request().thesis
    return Recommendation(
        recommendation_id=thesis_id,
        symbol=thesis.symbol or "NVDA",
        strategy=thesis.playbook_id,
        created_at=thesis.observed_at,
        entry=float(thesis.entry_price or 100),
        stop=float(thesis.stop_price or 99),
        target_1r=float(thesis.targets[0].price),
        target_2r=float(thesis.targets[1].price),
        state=RecommendationState.SETUP,
        rationale=thesis.rationale,
    )
