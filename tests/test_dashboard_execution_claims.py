from __future__ import annotations

import os
from pathlib import Path

import pytest

from trading_agent.dashboard_execution_claims import (
    InteractiveClaimStore,
    InvalidInteractiveClaimStoreError,
)

INTERACTION_ID = "019c0014-f0f5-7000-8000-000000000001"
REQUEST_SHA = "a" * 64


def test_duplicate_claim_and_restart_never_allocate_a_second_process(tmp_path: Path) -> None:
    # Given: one durable interactive claim
    database = tmp_path / "claims.sqlite3"
    first = InteractiveClaimStore(database)
    assert first.claim(INTERACTION_ID, "day_trading", "conversation", REQUEST_SHA)
    assert first.mark_running(INTERACTION_ID)

    # When: duplicate delivery arrives after a process-start claim and a restart
    restarted = InteractiveClaimStore(database)

    # Then: the duplicate is rejected and recovery closes uncertainty without retry
    assert not restarted.claim(INTERACTION_ID, "day_trading", "conversation", REQUEST_SHA)
    assert restarted.recover_incomplete() == 1
    claim = restarted.get(INTERACTION_ID)
    assert claim is not None
    assert claim.state == "uncertain"
    assert claim.process_starts == 1


def test_compare_and_set_rejects_terminal_replacement(tmp_path: Path) -> None:
    # Given: one completed claim
    store = InteractiveClaimStore(tmp_path / "claims.sqlite3")
    assert store.claim(INTERACTION_ID, "market_context", "directed", REQUEST_SHA)
    assert store.mark_running(INTERACTION_ID)
    assert store.mark_terminal(INTERACTION_ID, "completed")

    # When / Then: stale or conflicting transitions cannot replace the terminal state
    assert not store.mark_terminal(INTERACTION_ID, "failed")
    assert not store.mark_running(INTERACTION_ID)


def test_unsafe_claim_database_fails_before_open(tmp_path: Path) -> None:
    # Given: a hard-linked would-be durable claim database
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"not-a-database")
    target.chmod(0o600)
    database = tmp_path / "claims.sqlite3"
    os.link(target, database)

    # When / Then: identity validation fails before SQLite reads it
    with pytest.raises(InvalidInteractiveClaimStoreError):
        InteractiveClaimStore(database)
