from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.local_browser_gateway import canonical_browser_request
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserResponse,
)
from trading_agent.local_browser_receipts import (
    InvalidLocalBrowserReceiptError,
    LocalBrowserReceiptConflictError,
    LocalBrowserReceiptStore,
    browser_receipt,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _request(url: str = "https://example.com/story") -> BrowserOpenRequest:
    return BrowserOpenRequest(request_id="a" * 64, url=url)


def _response() -> BrowserResponse:
    return BrowserResponse(
        request_id="a" * 64,
        action=BrowserAction.OPEN,
        observation=BrowserPageObservation(
            target_id="target-1",
            url="https://example.com/story",
            title="Story",
            visible_text="bounded text",
            captured_at=NOW,
        ),
    )


def test_receipt_database_is_private_append_only_and_digest_only(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "receipts.sqlite3"
    request, response = _request(), _response()
    with LocalBrowserReceiptStore(database) as store:
        store.append(browser_receipt(request, response, NOW))
    assert stat.S_IMODE(os.lstat(database).st_mode) == 0o600
    assert os.lstat(database).st_nlink == 1
    with sqlite3.connect(database) as connection:
        triggers = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        }
        assert triggers == {
            "local_browser_requests_no_update",
            "local_browser_requests_no_delete",
            "local_browser_responses_no_update",
            "local_browser_responses_no_delete",
        }
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE local_browser_requests SET action = 'read'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM local_browser_responses")
        request_columns = {row[1] for row in connection.execute("PRAGMA table_info(local_browser_requests)").fetchall()}
    assert "request_json" not in request_columns
    assert "headers" not in request_columns
    assert "cookies" not in request_columns


def test_same_request_id_with_changed_payload_conflicts(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "receipts.sqlite3"
    request, response = _request(), _response()
    with LocalBrowserReceiptStore(database) as store:
        digest = hashlib.sha256(canonical_browser_request(request)).hexdigest()
        store.append(browser_receipt(request, response, NOW))
        assert store.replay(request.request_id, digest) == response
        changed = _request("https://example.org/changed")
        with pytest.raises(LocalBrowserReceiptConflictError):
            _ = store.replay(
                changed.request_id,
                hashlib.sha256(canonical_browser_request(changed)).hexdigest(),
            )


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "wrong-owner"))
def test_receipt_database_rejects_untrusted_identity(tmp_path: Path, kind: str) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "receipts.sqlite3"
    owner_id = os.geteuid()
    if kind == "wrong-owner":
        owner_id += 1
    else:
        target = tmp_path / "target.sqlite3"
        target.touch(mode=0o600)
        if kind == "symlink":
            database.symlink_to(target)
        else:
            os.link(target, database)
    with pytest.raises(InvalidLocalBrowserReceiptError), LocalBrowserReceiptStore(database, owner_id=owner_id):
        pass


def test_receipt_database_rejects_version_spoof_without_append_only_triggers(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "receipts.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE local_browser_requests (request_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    database.chmod(0o600)
    with pytest.raises(InvalidLocalBrowserReceiptError), LocalBrowserReceiptStore(database):
        pass


@pytest.mark.parametrize("interruption", (InvalidLocalBrowserReceiptError, KeyboardInterrupt, SystemExit))
def test_receipt_execution_lease_releases_on_error_or_process_interruption(
    tmp_path: Path, interruption: type[BaseException]
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    with LocalBrowserReceiptStore(path) as first, LocalBrowserReceiptStore(path) as second:
        with pytest.raises(interruption), first.execution_lease():
            raise interruption()
        acquired = threading.Event()

        def acquire() -> None:
            with second.execution_lease():
                acquired.set()

        worker = threading.Thread(target=acquire, daemon=True)
        worker.start()
        worker.join(timeout=1.0)
        assert acquired.is_set()


def test_receipt_execution_lease_rejects_database_name_replacement_before_release(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    with (
        LocalBrowserReceiptStore(path) as store,
        pytest.raises(InvalidLocalBrowserReceiptError),
        store.execution_lease(),
    ):
        path.unlink()
        path.touch(mode=0o600)


def test_receipt_execution_lease_rejects_parent_replacement_before_release(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    moved = tmp_path / "moved"
    with LocalBrowserReceiptStore(path) as store:
        with pytest.raises(InvalidLocalBrowserReceiptError), store.execution_lease():
            state.rename(moved)
            state.mkdir(mode=0o700)
        state.rmdir()
        moved.rename(state)
        with store.execution_lease():
            pass


def test_two_stores_repeatedly_initialize_fresh_database(tmp_path: Path) -> None:
    for iteration in range(50):
        failures, entered, live_threads = _race_fresh_store_initialization(tmp_path / str(iteration))
        assert entered == 2
        assert live_threads == 0
        assert failures == ()


def _race_fresh_store_initialization(state: Path) -> tuple[tuple[str, ...], int, int]:
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    start_gate = threading.Barrier(2)
    failures: list[str] = []
    entries: list[None] = []

    def open_store() -> None:
        start_gate.wait(timeout=2.0)
        try:
            with LocalBrowserReceiptStore(path):
                entries.append(None)
        except InvalidLocalBrowserReceiptError as error:
            failures.append(error.reason)

    workers = tuple(threading.Thread(target=open_store) for _index in range(2))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2.0)
    return tuple(failures), len(entries), sum(worker.is_alive() for worker in workers)
