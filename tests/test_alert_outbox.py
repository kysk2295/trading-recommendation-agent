from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import run_trading_agent_replay
from trading_agent import replay
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore


def test_alert_outbox_persists_a_complete_card_without_duplicates(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    created_at = dt.datetime(2026, 7, 13, 9, 36, tzinfo=ZoneInfo("America/New_York"))
    store.save(
        Recommendation(
            "alert-1",
            "TEST",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.SETUP,
            "5분 ORB와 거래량 재확대",
        )
    )
    queued_at = created_at + dt.timedelta(seconds=5)

    first_count = replay.write_alert_outbox(tmp_path, store, queued_at)
    (tmp_path / "recommendation_alerts.jsonl").unlink()
    (tmp_path / "recommendation_alerts_ko.md").unlink()
    second_count = replay.write_alert_outbox(
        tmp_path,
        store,
        queued_at + dt.timedelta(minutes=1),
    )

    json_lines = (tmp_path / "recommendation_alerts.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(json_lines[0])
    markdown = (tmp_path / "recommendation_alerts_ko.md").read_text(encoding="utf-8")
    assert first_count == 1
    assert second_count == 0
    assert len(store.alerts()) == 1
    assert len(json_lines) == 1
    assert payload["recommendation_id"] == "alert-1"
    assert payload["symbol"] == "TEST"
    assert payload["entry"] == 10.5
    assert payload["stop"] == 10.0
    assert payload["target_1r"] == 11.0
    assert payload["target_2r"] == 11.5
    assert payload["paper_only"] is True
    assert "알림 이후 새 완료 1분봉" in payload["entry_condition"]
    assert "진입 전 10.0000 이하" in payload["invalidation_condition"]
    assert "TEST · opening_range_breakout" in markdown
    assert "조건부 진입: 10.5000" in markdown
    assert "수익 보장" in markdown


def test_alert_outbox_writes_an_explicit_empty_projection(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    queued_at = dt.datetime(2026, 7, 13, 9, 36, tzinfo=ZoneInfo("America/New_York"))

    count = replay.write_alert_outbox(tmp_path, store, queued_at)

    assert count == 0
    assert (tmp_path / "recommendation_alerts.jsonl").read_text(encoding="utf-8") == ""
    assert "새 추천 없음" in (tmp_path / "recommendation_alerts_ko.md").read_text(encoding="utf-8")


def test_alert_outbox_does_not_queue_a_recommendation_before_the_cutoff(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    created_at = dt.datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    store.save(
        Recommendation(
            "stale-alert",
            "OLD",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.SETUP,
            "과거 추천",
        )
    )

    count = replay.write_alert_outbox(
        tmp_path,
        store,
        created_at + dt.timedelta(minutes=30),
        created_at + dt.timedelta(minutes=25),
    )

    assert count == 0
    assert store.alerts() == ()


def test_alert_card_exposes_the_first_fully_post_alert_minute(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    created_at = dt.datetime(
        2026,
        7,
        13,
        9,
        36,
        5,
        tzinfo=ZoneInfo("America/New_York"),
    )
    store.save(
        Recommendation(
            "causal-alert",
            "TEST",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.SETUP,
            "5분 ORB와 거래량 재확대",
        )
    )

    queued_at = created_at + dt.timedelta(minutes=1)

    _ = replay.write_alert_outbox(tmp_path, store, queued_at)

    payload = json.loads(store.alerts()[0].payload_json)
    assert payload["first_eligible_bar_at"] == "2026-07-13T09:38:00-04:00"
    assert "첫 체결 평가 봉: 2026-07-13T09:38:00-04:00" in (tmp_path / "recommendation_alerts_ko.md").read_text(
        encoding="utf-8"
    )


def test_alert_queue_timestamp_never_predates_the_recommendation(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    created_at = dt.datetime(
        2026,
        7,
        13,
        9,
        36,
        5,
        tzinfo=ZoneInfo("America/New_York"),
    )
    store.save(
        Recommendation(
            "queue-causality",
            "TEST",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.SETUP,
            "5분 ORB와 거래량 재확대",
        )
    )

    _ = replay.write_alert_outbox(
        tmp_path,
        store,
        created_at - dt.timedelta(seconds=5),
    )

    alert = store.alerts()[0]
    payload = json.loads(alert.payload_json)
    assert alert.queued_at == created_at
    assert payload["queued_at"] == created_at.isoformat()


def test_replay_cli_writes_the_alert_projection(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples" / "example_intraday.csv"
    output = tmp_path / "replay"

    run_trading_agent_replay.main(str(source), str(output), 5)

    json_lines = (output / "recommendation_alerts.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(json_lines[0])
    assert len(json_lines) == 1
    assert payload["risk_per_share"] == 0.235325
    assert payload["queued_at"] == payload["created_at"]
    assert payload["first_eligible_bar_at"] == payload["created_at"]
    assert "조건부 진입" in (output / "recommendation_alerts_ko.md").read_text(encoding="utf-8")
