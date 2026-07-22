from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_systematic_regime_engine import STRATEGY_VERSION, _source
from trading_agent.systematic_regime_engine import build_systematic_card, replay_systematic_regime
from trading_agent.systematic_regime_store import (
    SystematicRegimeConflictError,
    SystematicRegimeStore,
)


def test_card_store_is_append_only_and_exact_replay_is_a_noop(tmp_path: Path) -> None:
    # Given: a current systematic recommendation card and private local store.
    source = _source("risk_on")
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)
    path = tmp_path / "systematic.sqlite3"
    store = SystematicRegimeStore(path)

    # When: the same immutable card is appended twice.
    with store.writer() as writer:
        first = writer.append_card(card)
        second = writer.append_card(card)

    # Then: only one canonical card exists and the file is owner-private.
    assert first is True
    assert second is False
    assert store.cards() == (card,)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute("UPDATE systematic_cards SET card_id = 'changed'")


def test_card_store_rejects_conflicting_content_for_the_same_id(tmp_path: Path) -> None:
    # Given: a committed card and a payload with the same identity but different replay evidence.
    source = _source("risk_on")
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)
    conflict = card.model_copy(update={"replay_id": "f" * 64})
    store = SystematicRegimeStore(tmp_path / "systematic.sqlite3")
    with store.writer() as writer:
        _ = writer.append_card(card)

    # When/Then: immutable identity conflict fails closed.
    with store.writer() as writer, pytest.raises(SystematicRegimeConflictError):
        _ = writer.append_card(conflict)
