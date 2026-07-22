from __future__ import annotations

import os
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_systematic_regime_engine import STRATEGY_VERSION, _source
from trading_agent.systematic_regime_engine import build_systematic_card, replay_systematic_regime
from trading_agent.systematic_regime_store import (
    InvalidSystematicRegimeStoreError,
    SystematicRegimeConflictError,
    SystematicRegimeStore,
    SystematicShadowOutcome,
)
from trading_agent.systematic_regime_store_sql import (
    InvalidSystematicRegimeSqliteError,
    systematic_reader_connection,
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


def test_store_rejects_database_symlink_before_touching_target(tmp_path: Path) -> None:
    # Given: the requested database name aliases a separate private file.
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"untouched")
    target.chmod(0o600)
    alias = tmp_path / "systematic.sqlite3"
    alias.symlink_to(target)

    # When/Then: the writer rejects the alias without changing the target.
    with pytest.raises(InvalidSystematicRegimeStoreError), SystematicRegimeStore(alias).writer():
        pass
    assert target.read_bytes() == b"untouched"


def test_store_rejects_database_and_lock_hard_links(tmp_path: Path) -> None:
    # Given: caller-controlled database and writer-lock names with multiple links.
    linked_database = tmp_path / "linked-database"
    linked_database.write_bytes(b"")
    linked_database.chmod(0o600)
    database = tmp_path / "systematic.sqlite3"
    os.link(linked_database, database)

    # When/Then: the database alias is rejected before SQLite initialization.
    with pytest.raises(InvalidSystematicRegimeStoreError), SystematicRegimeStore(database).writer():
        pass


def test_rejected_database_alias_does_not_leak_descriptors(tmp_path: Path) -> None:
    # Given: a hard-linked database that repeatedly fails private-file validation.
    linked_database = tmp_path / "linked-database"
    linked_database.write_bytes(b"")
    linked_database.chmod(0o600)
    database = tmp_path / "systematic.sqlite3"
    os.link(linked_database, database)
    before = len(tuple(Path("/dev/fd").iterdir()))

    # When: a long-lived caller retries the rejected writer boundary.
    for _ in range(25):
        with pytest.raises(InvalidSystematicRegimeStoreError), SystematicRegimeStore(
            database
        ).writer():
            pass

    # Then: rejected descriptors were closed instead of accumulating.
    assert len(tuple(Path("/dev/fd").iterdir())) <= before + 1

    database.unlink()
    (tmp_path / "systematic.sqlite3.writer.lock").unlink()
    lock_source = tmp_path / "lock-source"
    lock_source.write_bytes(b"")
    lock_source.chmod(0o600)
    os.link(lock_source, tmp_path / "systematic.sqlite3.writer.lock")

    # When/Then: a hard-linked lease cannot become the single-writer authority.
    with pytest.raises(InvalidSystematicRegimeStoreError), SystematicRegimeStore(database).writer():
        pass


def test_store_enforces_card_foreign_key_for_outcome(tmp_path: Path) -> None:
    # Given: a valid outcome whose parent card was never appended.
    source = _source("risk_on")
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)
    outcome = SystematicShadowOutcome(
        card_id=card.card_id,
        target_session=card.target_session,
        observed_at=source.observed_at,
        candidate_symbols=card.candidate_symbols,
        no_position=False,
        net_return_bps=Decimal("1"),
        source_key=source.source_key,
    )
    store = SystematicRegimeStore(tmp_path / "systematic.sqlite3")

    # When/Then: the public writer cannot persist an orphan outcome.
    with store.writer() as writer, pytest.raises(SystematicRegimeConflictError):
        _ = writer.append_outcome(outcome)
    assert store.outcomes() == ()


def test_store_rejects_missing_append_only_trigger(tmp_path: Path) -> None:
    # Given: a valid store whose card update trigger was removed out of band.
    source = _source("risk_on")
    card = build_systematic_card(source, replay_systematic_regime(source), STRATEGY_VERSION)
    path = tmp_path / "systematic.sqlite3"
    store = SystematicRegimeStore(path)
    with store.writer() as writer:
        _ = writer.append_card(card)
    with sqlite3.connect(path) as connection:
        _ = connection.execute("DROP TRIGGER systematic_cards_no_update")

    # When/Then: query-only replay refuses the weakened schema.
    with pytest.raises(InvalidSystematicRegimeStoreError):
        _ = store.cards()


def test_reader_rejects_stored_foreign_key_violation(tmp_path: Path) -> None:
    # Given: an otherwise canonical schema containing an out-of-band orphan row.
    path = tmp_path / "systematic.sqlite3"
    store = SystematicRegimeStore(path)
    with store.writer():
        pass
    with sqlite3.connect(path) as connection:
        _ = connection.execute(
            "INSERT INTO systematic_outcomes (card_id, payload_json) VALUES ('missing', '{}')"
        )

    # When/Then: schema replay checks stored referential integrity before payload parsing.
    with pytest.raises(InvalidSystematicRegimeSqliteError), systematic_reader_connection(path):
        pass


def test_reader_connection_stays_bound_during_a_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two canonical databases and a path swap during sqlite3.connect.
    path = tmp_path / "systematic.sqlite3"
    alternate = tmp_path / "alternate.sqlite3"
    held = tmp_path / "held.sqlite3"
    with SystematicRegimeStore(path).writer(), SystematicRegimeStore(alternate).writer():
        pass
    with sqlite3.connect(path) as connection:
        _ = connection.execute("PRAGMA application_id = 111")
    with sqlite3.connect(alternate) as connection:
        _ = connection.execute("PRAGMA application_id = 777")
    original_connect = sqlite3.connect

    def swapping_connect(
        database: str,
        *,
        uri: bool = False,
        timeout: float = 5.0,
    ) -> sqlite3.Connection:
        path.replace(held)
        alternate.replace(path)
        try:
            return original_connect(database, uri=uri, timeout=timeout)
        finally:
            path.replace(alternate)
            held.replace(path)

    monkeypatch.setattr(sqlite3, "connect", swapping_connect)

    # When: the query-only reader opens across the ABA swap.
    with systematic_reader_connection(path) as connection:
        application_id = connection.execute("PRAGMA application_id").fetchone()

    # Then: it queried the validated descriptor, never the temporary pathname target.
    assert application_id == (111,)
