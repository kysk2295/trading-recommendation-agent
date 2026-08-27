from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

import trading_agent.kr_social_signal_store as signal_store
from tests.test_kr_social_signal import _request, _selected_posts
from trading_agent.kr_social_signal_models import canonical_kr_social_signal_json, normalize_kr_social_signal
from trading_agent.kr_social_signal_store import (
    InvalidKrSocialSignalStoreError,
    KrSocialSignalConflictError,
    KrSocialSignalStore,
)


def _signal(theme: str = "Semiconductor demand"):
    posts = _selected_posts()
    return normalize_kr_social_signal(_request(posts).model_copy(update={"theme": theme}), posts)


def test_append_replays_immutably_and_queries_task_order(tmp_path: Path) -> None:
    # Given: two content-addressed signals for the same task.
    first = _signal()
    second = _signal("Semiconductor supply-chain demand")
    store = KrSocialSignalStore(tmp_path / "signals.sqlite3")
    # When: both signals are appended and the first is replayed.
    assert store.append(first) is True
    assert store.append(second) is True
    assert store.append(first) is False
    # Then: exact replay is idempotent and query order is deterministic.
    assert store.get(first.signal_id) == first
    assert store.signals_for_task(first.task_id) == tuple(
        sorted((first, second), key=lambda item: (item.normalized_at, item.signal_id))
    )


def test_store_rejects_forged_divergent_identity(tmp_path: Path) -> None:
    # Given: an otherwise valid signal with a forged identity after construction.
    signal = _signal()
    forged = signal.model_copy(update={"theme": "Different claim"})
    store = KrSocialSignalStore(tmp_path / "signals.sqlite3")
    # When/Then: a divergent canonical payload cannot be appended under the original ID.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = store.append(forged)


def test_append_conflicts_on_divergent_persisted_row_without_rewrite(tmp_path: Path) -> None:
    # Given: an exact target payload/hash row whose persisted task projection is changed.
    path = tmp_path / "signals.sqlite3"
    store = KrSocialSignalStore(path)
    baseline = _signal()
    target = _signal("Independent semiconductor demand confirmation")
    assert store.append(baseline)
    payload = canonical_kr_social_signal_json(target)
    row = (
        target.signal_id,
        "b" * 64,
        target.symbol,
        target.model_dump(mode="json")["normalized_at"],
        hashlib.sha256(payload.encode("ascii")).hexdigest(),
        payload,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO kr_social_signals VALUES (?,?,?,?,?,?)", row)
    # When/Then: append conflicts and cannot rewrite the divergent immutable row.
    with pytest.raises(KrSocialSignalConflictError):
        _ = store.append(target)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT signal_id,task_id,symbol,normalized_at,payload_sha256,payload_json "
                "FROM kr_social_signals WHERE signal_id=?",
                (target.signal_id,),
            ).fetchone()
            == row
        )
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = store.get(target.signal_id)


def test_payload_or_hash_tamper_is_invalid_to_reads_and_conflicts_to_append(tmp_path: Path) -> None:
    # Given: a stored target payload whose hash no longer binds its exact canonical JSON.
    path = tmp_path / "signals.sqlite3"
    store = KrSocialSignalStore(path)
    baseline = _signal()
    target = _signal("Independent semiconductor demand confirmation")
    assert store.append(baseline)
    payload = canonical_kr_social_signal_json(target)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO kr_social_signals VALUES (?,?,?,?,?,?)",
            (
                target.signal_id,
                target.task_id,
                target.symbol,
                target.model_dump(mode="json")["normalized_at"],
                "0" * 64,
                payload,
            ),
        )
    # When/Then: reads reject the corruption and appends report immutable identity conflict.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = store.get(target.signal_id)
    with pytest.raises(KrSocialSignalConflictError):
        _ = store.append(target)


def test_store_enforces_private_append_only_database(tmp_path: Path) -> None:
    # Given: one persisted immutable signal.
    path = tmp_path / "private" / "signals.sqlite3"
    signal = _signal()
    store = KrSocialSignalStore(path)
    assert store.append(signal)
    # When: filesystem and mutation contracts are inspected.
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE kr_social_signals SET symbol='000000'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM kr_social_signals")
    # Then: the authority file and parent are private and records cannot change.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "wrong_mode"))
def test_store_rejects_untrusted_database_file(tmp_path: Path, kind: str) -> None:
    # Given: a database path with an unsafe file identity.
    path = tmp_path / "signals.sqlite3"
    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    else:
        target.rename(path)
        path.chmod(0o640)
    # When/Then: no append can trust the unsafe authority file.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = KrSocialSignalStore(path).append(_signal())


def test_reader_rejects_broken_database_symlink(tmp_path: Path) -> None:
    # Given: a dangling link presented as the store database.
    path = tmp_path / "signals.sqlite3"
    path.symlink_to(tmp_path / "missing.sqlite3")
    # When/Then: read paths fail closed rather than treating it as absent.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = KrSocialSignalStore(path).get("a" * 64)


def test_store_rejects_unsafe_parent_mode_and_owner_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a nonprivate parent and then a persisted database observed as another owner's file.
    parent = tmp_path / "unsafe"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = KrSocialSignalStore(parent / "signals.sqlite3").append(_signal())
    path = tmp_path / "signals.sqlite3"
    store = KrSocialSignalStore(path)
    assert store.append(_signal())
    monkeypatch.setattr(signal_store, "_current_owner_id", lambda: os.getuid() + 1)
    # When/Then: neither unsafe parent permissions nor a UID mismatch can open the authority.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = store.get(_signal().signal_id)


def test_query_path_uses_read_only_uri_and_query_only_pragma(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an initialized store and a narrow reader-connection probe.
    path = tmp_path / "signals.sqlite3"
    store = KrSocialSignalStore(path)
    signal = _signal()
    assert store.append(signal)
    calls: list[str] = []
    pragmas: list[str] = []
    real_connect = signal_store.sqlite3.connect

    class ReaderProbe:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(self, statement: str, parameters: tuple[str, ...] = ()) -> sqlite3.Cursor:
            if statement == "PRAGMA query_only=ON":
                pragmas.append(statement)
            return self.connection.execute(statement, parameters)

        def close(self) -> None:
            self.connection.close()

    def connect(database: str, *, uri: bool = False) -> sqlite3.Connection | ReaderProbe:
        connection = real_connect(database, uri=uri)
        if uri:
            calls.append(database)
            return ReaderProbe(connection)
        return connection

    monkeypatch.setattr(signal_store.sqlite3, "connect", connect)
    # When: the public query path reads the immutable signal.
    assert store.get(signal.signal_id) == signal
    # Then: it opens a read-only URI and enables SQLite's query-only mode.
    assert calls == [signal_store.sqlite_read_only_uri(path)]
    assert pragmas == ["PRAGMA query_only=ON"]


def test_reads_use_query_only_schema_checked_authority(tmp_path: Path) -> None:
    # Given: a private signal store whose immutable schema is then tampered with.
    path = tmp_path / "signals.sqlite3"
    store = KrSocialSignalStore(path)
    signal = _signal()
    assert store.append(signal)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER kr_social_signals_no_delete")
    # When/Then: read paths reject a schema that could permit mutation.
    with pytest.raises(InvalidKrSocialSignalStoreError):
        _ = store.get(signal.signal_id)


def test_concurrent_same_signal_append_is_idempotent(tmp_path: Path) -> None:
    # Given: two writers released together for the same content-addressed signal.
    path = tmp_path / "signals.sqlite3"
    signal = _signal()
    start = threading.Barrier(2)
    results: list[bool] = []
    failures: list[str] = []

    def append() -> None:
        start.wait(timeout=2.0)
        try:
            results.append(KrSocialSignalStore(path).append(signal))
        except InvalidKrSocialSignalStoreError as error:
            failures.append(str(error))

    workers = tuple(threading.Thread(target=append) for _ in range(2))
    # When: both writers attempt the exact same append.
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)
    # Then: one append wins and one replay is observed without corruption.
    assert sorted(results) == [False, True]
    assert failures == [] and all(not worker.is_alive() for worker in workers)
