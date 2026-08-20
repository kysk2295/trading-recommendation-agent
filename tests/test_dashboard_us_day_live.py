from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_us_day_signal_admission import _eligible_request
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis
from trading_agent.us_day_thesis_store import UsDayThesisStore

NOW = dt.datetime(2026, 8, 20, 14, 7, tzinfo=dt.UTC)


def test_dashboard_shows_recommendation_to_paper_lineage(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    no_trade = _no_trade(recommendation.observed_at)
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    assert store.publish_thesis(no_trade)
    _append_completed_lifecycle(store, recommendation)
    _publish_json(
        outputs / "us_day" / "agent_versions.json",
        [
            {
                "version_id": recommendation.agent_version_id,
                "deployment_state": "champion",
                "observed_at": NOW.isoformat(),
            },
            {"version_id": "c" * 64, "deployment_state": "shadow", "observed_at": NOW.isoformat()},
        ],
    )
    _publish_json(
        outputs / "us_day" / "close_reviews.json",
        [{"thesis_id": recommendation.thesis_id, "observed_at": NOW.isoformat(), "status": "reviewed"}],
    )
    _publish_json(
        outputs / "us_day" / "market_regime.json",
        [{"label": "risk_on", "observed_at": NOW.isoformat()}],
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    markets = {item.item_id: item.value for item in snapshot.workspaces.markets.items}
    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert markets["day.regime"] == "risk_on"
    assert markets["day.theme.1"] == "semiconductor_infrastructure · leading"
    assert markets["day.leader.1"] == "NVDA · leader"
    assert markets["day.recommendation.NVDA"] == "entry 200.05 · stop 199.5 · targets 200.60/201.15"
    assert markets["day.thesis_change.NVDA"] == "close"
    assert markets["day.no_trade.1"] == "NO_TRADE · setup_not_confirmed"
    assert markets["day.champion"] == recommendation.agent_version_id[:12]
    assert markets["day.shadow.1"] == ("c" * 12)
    assert paper["day.paper.NVDA"] == "filled · protected · reconciled"
    assert paper["day.paper_exit.NVDA"] == "closed"
    assert paper["day.close_review.NVDA"] == "reviewed"
    assert "day-agent-live-reader-v1" in snapshot.projection.reader_versions
    assert any(edge.kind == "executed_as" for edge in snapshot.traces.edges)


def test_dashboard_blocks_only_day_items_for_stale_or_corrupt_source(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    stale = _no_trade(NOW - dt.timedelta(hours=2))
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(stale)

    stale_snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)
    stale_item = next(item for item in stale_snapshot.workspaces.markets.items if item.item_id == "day.source")
    assert stale_item.state == "blocked"
    assert stale_snapshot.workspaces.markets.state != "corrupt"

    artifact = outputs / "us_day" / "theses" / "theses" / f"{stale.thesis_id}.json"
    artifact.chmod(0o644)
    corrupt_snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)
    corrupt_item = next(item for item in corrupt_snapshot.workspaces.markets.items if item.item_id == "day.source")
    assert corrupt_item.state == "corrupt"
    assert corrupt_snapshot.workspaces.paper.state != "corrupt"


def test_dashboard_redacts_and_limits_day_items_newest_actionable_first(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    for index in range(30):
        thesis = _no_trade(NOW - dt.timedelta(minutes=index), index=index)
        assert store.publish_thesis(thesis)
    _publish_json(
        outputs / "us_day" / "agent_versions.json",
        [{"version_id": "a" * 64, "deployment_state": "champion", "observed_at": NOW.isoformat()}],
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    day_items = tuple(item for item in snapshot.workspaces.markets.items if item.item_id.startswith("day."))
    assert len(snapshot.workspaces.markets.items) <= 24
    assert len(day_items) <= 24
    assert all("secret" not in (item.value or "") for item in day_items)
    assert snapshot.workspaces.markets.truncated is True


def _day_outputs(tmp_path: Path) -> Path:
    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True, mode=0o700)
    return outputs


def _append_completed_lifecycle(store: UsDayThesisStore, thesis: UsDayTradeThesis) -> None:
    parent = thesis.thesis_id
    for offset, note in enumerate(("entry_acknowledged", "protective_oco_acknowledged", "flat", "reconciled"), start=1):
        change = UsDayThesisChange.create(
            thesis_id=thesis.thesis_id,
            parent_event_id=parent,
            kind=ThesisChangeKind.CLOSE if note == "reconciled" else ThesisChangeKind.HOLD,
            occurred_at=thesis.observed_at + dt.timedelta(seconds=offset),
            note=note,
        )
        assert store.publish_change(change)
        parent = change.event_id


def _no_trade(observed_at: dt.datetime, *, index: int = 0) -> UsDayTradeThesis:
    return UsDayTradeThesis.create(
        decision=DayTradeDecision.NO_TRADE,
        situation_id=f"{index + 1:064x}",
        agent_version_id="a" * 64,
        playbook_id="leader_breakout",
        theme_id="c" * 64,
        catalyst_event_id="d" * 64,
        flow_inference_kind=None,
        theme_name="semiconductor_infrastructure",
        symbol=None,
        entry_price=None,
        stop_price=None,
        targets=(),
        invalidation_rule="entry conditions are absent.",
        confidence_bps=2500,
        evidence_refs=(),
        observed_at=observed_at,
        valid_until=observed_at + dt.timedelta(minutes=1),
        reason_code="setup_not_confirmed",
    )


def _publish_json(path: Path, value: list[dict[str, str]]) -> None:
    assert publish_private_immutable_text(path, json.dumps(value, separators=(",", ":")))
