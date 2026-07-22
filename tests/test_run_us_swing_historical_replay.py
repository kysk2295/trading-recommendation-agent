from __future__ import annotations

import datetime as dt
import json
import shutil
import stat
from pathlib import Path

import pytest

from run_us_swing_historical_replay import main
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_shadow_review_models import SwingShadowReviewerAction
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowReader, SwingShadowStore
from trading_agent.us_swing_historical_replay import (
    HistoricalSwingFixtureScanner,
    SwingHistoricalReplayFixture,
)

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_MANIFEST = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
REPLAY_FIXTURES = ROOT / "examples" / "us_swing_historical_replay"
SIGNAL_SESSION = dt.date(2026, 7, 17)
TERMINAL_SESSION = dt.date(2026, 7, 20)


def test_cli_replays_causal_swing_flow_through_cards_terminal_and_reviewer(
    tmp_path: Path,
) -> None:
    # Given: two consecutive point-in-time daily snapshots for one Swing strategy.
    delivery = tmp_path / "delivery.sqlite3"
    shadow = tmp_path / "shadow.sqlite3"
    reviews = tmp_path / "reviews.sqlite3"
    output = tmp_path / "output"
    arguments = _arguments(tmp_path)

    # When: the historical replay is driven through its real CLI boundary.
    return_code = main(arguments, runtime_code_version="test_code_v1")

    # Then: recommendation/no-recommendation cards, terminal shadow, and review all exist.
    assert return_code == 0
    assert tuple(event.kind for event in HermesDeliveryStore(delivery).events()) == (
        HermesDeliveryKind.WATCH,
        HermesDeliveryKind.NO_RECOMMENDATION,
        HermesDeliveryKind.EXIT,
    )
    signal = SwingShadowReader(shadow).signals()[0]
    assert tuple(event.kind for event in SwingShadowReader(shadow).events(signal.signal_id)) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.STOPPED,
    )
    review = SwingShadowReviewStore(reviews).events()[0].event
    assert review.reviewer_action is SwingShadowReviewerAction.CONTINUE_COLLECTION
    assert review.automatic_state_change_allowed is False
    report = output / "us_swing_historical_replay_ko.md"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    rendered = report.read_text(encoding="utf-8")
    assert "causal snapshots: 2" in rendered
    assert "recommendation cards: 1" in rendered
    assert "no-recommendation cards: 1" in rendered
    assert "reviewer evidence: 1" in rendered
    assert "external broker mutations: 0" in rendered


def test_cli_exact_replay_skips_scheduler_and_preserves_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the complete two-session replay already exists in immutable stores.
    arguments = _arguments(tmp_path)
    assert main(arguments, runtime_code_version="test_code_v1") == 0
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    reviews = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")
    before = (len(delivery.events()), len(reviews.events()))

    def unexpected_scheduler_tick(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("completed replay must not run the operating scheduler")

    monkeypatch.setattr(
        "trading_agent.us_swing_historical_replay.run_us_swing_operating_tick",
        unexpected_scheduler_tick,
    )

    # When: the exact same CLI replay is requested again.
    return_code = main(arguments, runtime_code_version="test_code_v1")

    # Then: it verifies source identity and succeeds without scheduling or adding events.
    assert return_code == 0
    assert (len(delivery.events()), len(reviews.events())) == before


def test_cli_rejects_completed_replay_from_a_different_runtime_code_version(
    tmp_path: Path,
) -> None:
    arguments = _arguments(tmp_path)
    assert main(arguments, runtime_code_version="test_code_v1") == 0
    before = _evidence_counts(tmp_path)

    return_code = main(arguments, runtime_code_version="test_code_v2")

    assert return_code == 1
    assert _evidence_counts(tmp_path) == before


def test_cli_rejects_completed_replay_after_fixture_content_changes(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fixtures"
    shutil.copytree(REPLAY_FIXTURES, fixture_root)
    arguments = _arguments(tmp_path, replay_fixtures=fixture_root)
    assert main(arguments, runtime_code_version="test_code_v1") == 0
    before = _evidence_counts(tmp_path)
    bars_path = fixture_root / TERMINAL_SESSION.isoformat() / "daily-bars.json"
    bars = json.loads(bars_path.read_text(encoding="utf-8"))
    bars[-1]["volume"] += 1
    bars_path.write_text(json.dumps(bars), encoding="utf-8")

    return_code = main(arguments, runtime_code_version="test_code_v1")

    assert return_code == 1
    assert _evidence_counts(tmp_path) == before


def test_cli_recovers_terminal_delivery_and_review_after_root_cards_only(tmp_path: Path) -> None:
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    shadow = SwingShadowStore(tmp_path / "shadow.sqlite3")
    fixtures = tuple(
        SwingHistoricalReplayFixture(session, REPLAY_FIXTURES / session.isoformat())
        for session in (SIGNAL_SESSION, TERMINAL_SESSION)
    )
    scanner = HistoricalSwingFixtureScanner(fixtures, shadow, delivery)
    for session in (SIGNAL_SESSION, TERMINAL_SESSION):
        _ = scanner.run(session)

    assert tuple(event.kind for event in delivery.events()) == (
        HermesDeliveryKind.WATCH,
        HermesDeliveryKind.NO_RECOMMENDATION,
    )
    assert SwingShadowReviewStore(tmp_path / "reviews.sqlite3").events() == ()

    return_code = main(_arguments(tmp_path), runtime_code_version="test_code_v1")

    assert return_code == 0
    assert tuple(event.kind for event in delivery.events()) == (
        HermesDeliveryKind.WATCH,
        HermesDeliveryKind.NO_RECOMMENDATION,
        HermesDeliveryKind.EXIT,
    )
    assert len(SwingShadowReviewStore(tmp_path / "reviews.sqlite3").events()) == 1


def _arguments(
    tmp_path: Path,
    *,
    replay_fixtures: Path = REPLAY_FIXTURES,
) -> tuple[str, ...]:
    return (
        "--fixture",
        f"{SIGNAL_SESSION.isoformat()}={replay_fixtures / SIGNAL_SESSION.isoformat()}",
        "--fixture",
        f"{TERMINAL_SESSION.isoformat()}={replay_fixtures / TERMINAL_SESSION.isoformat()}",
        "--research-manifest",
        str(RESEARCH_MANIFEST),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--shadow-ledger",
        str(tmp_path / "shadow.sqlite3"),
        "--delivery-store",
        str(tmp_path / "delivery.sqlite3"),
        "--review-ledger",
        str(tmp_path / "reviews.sqlite3"),
        "--output-dir",
        str(tmp_path / "output"),
    )


def _evidence_counts(tmp_path: Path) -> tuple[int, int, int]:
    return (
        len(HermesDeliveryStore(tmp_path / "delivery.sqlite3").events()),
        len(SwingShadowReader(tmp_path / "shadow.sqlite3").signals()),
        len(SwingShadowReviewStore(tmp_path / "reviews.sqlite3").events()),
    )
