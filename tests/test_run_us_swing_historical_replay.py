from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from run_us_swing_historical_replay import main
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_shadow_review_models import SwingShadowReviewerAction
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowReader

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


def test_cli_exact_replay_opens_no_fixture_and_preserves_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the complete two-session replay already exists in immutable stores.
    arguments = _arguments(tmp_path)
    assert main(arguments, runtime_code_version="test_code_v1") == 0
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    reviews = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")
    before = (len(delivery.events()), len(reviews.events()))

    def unexpected_fixture_open(_root: Path, *, session_date: dt.date) -> None:
        raise AssertionError(session_date)

    monkeypatch.setattr(
        "trading_agent.us_swing_historical_replay.load_swing_daily_source",
        unexpected_fixture_open,
    )

    # When: the exact same CLI replay is requested again.
    return_code = main(arguments, runtime_code_version="test_code_v1")

    # Then: it succeeds from stored evidence without reopening data or adding events.
    assert return_code == 0
    assert (len(delivery.events()), len(reviews.events())) == before


def _arguments(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--fixture",
        f"{SIGNAL_SESSION.isoformat()}={REPLAY_FIXTURES / SIGNAL_SESSION.isoformat()}",
        "--fixture",
        f"{TERMINAL_SESSION.isoformat()}={REPLAY_FIXTURES / TERMINAL_SESSION.isoformat()}",
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
