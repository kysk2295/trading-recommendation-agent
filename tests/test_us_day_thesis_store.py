from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis
from trading_agent.us_day_thesis_store import InvalidUsDayThesisStoreError, UsDayThesisStore


def test_store_publishes_original_and_append_only_content_addressed_change(tmp_path: Path) -> None:
    store = UsDayThesisStore(tmp_path)
    thesis = _terminal_thesis()
    assert store.publish_thesis(thesis) is True
    assert store.publish_thesis(thesis) is False
    assert store.theses() == (thesis,)
    change = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=thesis.thesis_id,
        kind=ThesisChangeKind.CANCEL_ENTRY,
        occurred_at=thesis.observed_at + dt.timedelta(minutes=1),
        note="진입 조건 소멸",
    )
    assert store.publish_change(change) is True
    assert store.changes(thesis.thesis_id) == (change,)
    assert (tmp_path / "theses" / f"{thesis.thesis_id}.json").stat().st_mode & 0o777 == 0o600


def test_store_fails_closed_for_divergence_symlink_and_hardlink(tmp_path: Path) -> None:
    store = UsDayThesisStore(tmp_path)
    thesis = _terminal_thesis()
    assert store.publish_thesis(thesis)
    artifact = tmp_path / "theses" / f"{thesis.thesis_id}.json"
    os.link(artifact, tmp_path / "alias.json")
    with pytest.raises(InvalidUsDayThesisStoreError):
        store.theses()


def test_change_history_is_one_terminal_aware_chain(tmp_path: Path) -> None:
    store = UsDayThesisStore(tmp_path)
    thesis = _terminal_thesis()
    assert store.publish_thesis(thesis)
    hold = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=thesis.thesis_id,
        kind=ThesisChangeKind.HOLD,
        occurred_at=thesis.observed_at + dt.timedelta(seconds=1),
        note="조건 유지",
    )
    close_same_parent = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=thesis.thesis_id,
        kind=ThesisChangeKind.CLOSE,
        occurred_at=thesis.observed_at + dt.timedelta(seconds=2),
        note="종료",
    )
    assert store.publish_change(hold)
    with pytest.raises(InvalidUsDayThesisStoreError):
        store.publish_change(close_same_parent)
    close = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=hold.event_id,
        kind=ThesisChangeKind.CLOSE,
        occurred_at=thesis.observed_at + dt.timedelta(seconds=2),
        note="종료",
    )
    assert store.publish_change(close)
    assert store.publish_change(close) is False
    after = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=close.event_id,
        kind=ThesisChangeKind.HOLD,
        occurred_at=thesis.observed_at + dt.timedelta(seconds=3),
        note="불가",
    )
    with pytest.raises(InvalidUsDayThesisStoreError):
        store.publish_change(after)


def test_relative_store_root_is_stable_after_cwd_change(tmp_path: Path) -> None:
    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        store = UsDayThesisStore(Path("relative-store"))
        thesis = _terminal_thesis()
        assert store.publish_thesis(thesis)
        os.chdir(original)
        assert store.thesis(thesis.thesis_id) == thesis
    finally:
        os.chdir(original)


def _terminal_thesis() -> UsDayTradeThesis:
    return UsDayTradeThesis.create(
        decision=DayTradeDecision.NO_TRADE,
        situation_id="b" * 64,
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
        invalidation_rule="현재 조건에서는 진입하지 않는다.",
        confidence_bps=2500,
        evidence_refs=(),
        observed_at=dt.datetime(2026, 8, 20, 14, 6, 5, tzinfo=dt.UTC),
        valid_until=dt.datetime(2026, 8, 20, 14, 7, tzinfo=dt.UTC),
        reason_code="setup_not_confirmed",
    )
