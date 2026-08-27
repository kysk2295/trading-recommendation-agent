from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Final, override

from pydantic import ValidationError

from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateSnapshot,
    KrLoopCandidateState,
    KrLoopReleaseEvent,
)
from trading_agent.kr_loop_engineer_serialization import (
    canonical_kr_loop_release_json,
    canonical_kr_loop_snapshot_json,
)
from trading_agent.kr_loop_engineer_store_sqlite import (
    InvalidKrLoopStoreSqliteError,
    open_kr_loop_database,
    prepare_kr_loop_database,
    require_kr_loop_schema,
)
from trading_agent.private_directory_identity import absolute_private_path

_TRANSITIONS: Final = {
    KrLoopCandidateState.DETECTED: frozenset({KrLoopCandidateState.CANDIDATE_READY, KrLoopCandidateState.REJECTED}),
    KrLoopCandidateState.CANDIDATE_READY: frozenset({KrLoopCandidateState.SHADOWING, KrLoopCandidateState.REJECTED}),
    KrLoopCandidateState.SHADOWING: frozenset(
        {KrLoopCandidateState.SHADOWING, KrLoopCandidateState.PROMOTED, KrLoopCandidateState.REJECTED}
    ),
    KrLoopCandidateState.PROMOTED: frozenset({KrLoopCandidateState.ROLLED_BACK}),
    KrLoopCandidateState.REJECTED: frozenset(),
    KrLoopCandidateState.ROLLED_BACK: frozenset(),
}
type _SnapshotRow = tuple[str, str, str | None, str, str, str, str]
type _ReleaseRow = tuple[str, int, str, str, str, str, str]


class InvalidKrLoopEngineerStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer store is invalid"


class KrLoopEngineerStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, snapshot: KrLoopCandidateSnapshot) -> bool:
        try:
            trusted = KrLoopCandidateSnapshot.model_validate(snapshot.model_dump(mode="python"))
            with open_kr_loop_database(self.path, write=True) as connection:
                prepare_kr_loop_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                inserted = _append_snapshot(connection, trusted)
                connection.commit()
                return inserted
        except (InvalidKrLoopStoreSqliteError, OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrLoopEngineerStoreError from None

    def append_release(self, snapshot: KrLoopCandidateSnapshot, release: KrLoopReleaseEvent) -> bool:
        try:
            trusted_snapshot = KrLoopCandidateSnapshot.model_validate(snapshot.model_dump(mode="python"))
            trusted_release = KrLoopReleaseEvent.model_validate(release.model_dump(mode="python"))
            _require_release_pair(trusted_snapshot, trusted_release)
            with open_kr_loop_database(self.path, write=True) as connection:
                prepare_kr_loop_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                inserted = _append_snapshot(connection, trusted_snapshot)
                release_inserted = _append_release(connection, trusted_release)
                if not inserted or not release_inserted:
                    raise InvalidKrLoopEngineerStoreError
                connection.commit()
                return True
        except (InvalidKrLoopStoreSqliteError, OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrLoopEngineerStoreError from None

    def history(self, candidate_id: str) -> tuple[KrLoopCandidateSnapshot, ...]:
        _require_sha(candidate_id)
        return tuple(item for item in self.snapshots() if item.candidate_id == candidate_id)

    def latest(self, candidate_id: str) -> KrLoopCandidateSnapshot | None:
        values = self.history(candidate_id)
        return values[-1] if values else None

    def snapshots(self) -> tuple[KrLoopCandidateSnapshot, ...]:
        if not self.path.exists():
            return ()
        try:
            with open_kr_loop_database(self.path, write=False) as connection:
                require_kr_loop_schema(connection)
                rows = connection.execute("SELECT * FROM kr_loop_snapshots ORDER BY rowid").fetchall()
            values = tuple(_decode_snapshot(row) for row in rows)
            _require_snapshot_chains(values)
            return values
        except (InvalidKrLoopStoreSqliteError, OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrLoopEngineerStoreError from None

    def releases(self) -> tuple[KrLoopReleaseEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            with open_kr_loop_database(self.path, write=False) as connection:
                require_kr_loop_schema(connection)
                rows = connection.execute("SELECT * FROM kr_loop_releases ORDER BY generation").fetchall()
            values = tuple(_decode_release(row) for row in rows)
            if tuple(item.generation for item in values) != tuple(range(1, len(values) + 1)):
                raise InvalidKrLoopEngineerStoreError
            return values
        except (InvalidKrLoopStoreSqliteError, OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrLoopEngineerStoreError from None


def _append_snapshot(connection: sqlite3.Connection, snapshot: KrLoopCandidateSnapshot) -> bool:
    row = _snapshot_row(snapshot)
    existing = connection.execute(
        "SELECT * FROM kr_loop_snapshots WHERE snapshot_id=?", (snapshot.snapshot_id,)
    ).fetchone()
    if existing is not None:
        if existing == row and _decode_snapshot(existing) == snapshot:
            return False
        raise InvalidKrLoopEngineerStoreError
    tail_row = connection.execute(
        "SELECT * FROM kr_loop_snapshots WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1",
        (snapshot.candidate_id,),
    ).fetchone()
    tail = None if tail_row is None else _decode_snapshot(tail_row)
    if not _transition_allowed(tail, snapshot):
        raise InvalidKrLoopEngineerStoreError
    connection.execute("INSERT INTO kr_loop_snapshots VALUES (?,?,?,?,?,?,?)", row)
    return True


def _append_release(connection: sqlite3.Connection, release: KrLoopReleaseEvent) -> bool:
    row = _release_row(release)
    existing = connection.execute("SELECT * FROM kr_loop_releases WHERE release_id=?", (release.release_id,)).fetchone()
    if existing is not None:
        return existing == row and _decode_release(existing) == release and False
    tail = connection.execute("SELECT generation FROM kr_loop_releases ORDER BY generation DESC LIMIT 1").fetchone()
    expected = 1 if tail is None else int(tail[0]) + 1
    if release.generation != expected:
        raise InvalidKrLoopEngineerStoreError
    connection.execute("INSERT INTO kr_loop_releases VALUES (?,?,?,?,?,?,?)", row)
    return True


def _transition_allowed(previous: KrLoopCandidateSnapshot | None, current: KrLoopCandidateSnapshot) -> bool:
    if previous is None:
        return current.previous_snapshot_id is None and current.state is KrLoopCandidateState.DETECTED
    return (
        current.previous_snapshot_id == previous.snapshot_id
        and current.bundle_id == previous.bundle_id
        and current.base_commit == previous.base_commit
        and current.allowed_paths == previous.allowed_paths
        and current.state in _TRANSITIONS[previous.state]
        and current.updated_at >= previous.updated_at
    )


def _require_release_pair(snapshot: KrLoopCandidateSnapshot, release: KrLoopReleaseEvent) -> None:
    if snapshot.candidate_id != release.candidate_id:
        raise InvalidKrLoopEngineerStoreError
    expected_state = (
        KrLoopCandidateState.PROMOTED if release.action.value == "promote" else KrLoopCandidateState.ROLLED_BACK
    )
    if snapshot.state is not expected_state:
        raise InvalidKrLoopEngineerStoreError


def _snapshot_row(snapshot: KrLoopCandidateSnapshot) -> _SnapshotRow:
    payload = canonical_kr_loop_snapshot_json(snapshot)
    return (
        snapshot.snapshot_id,
        snapshot.candidate_id,
        snapshot.previous_snapshot_id,
        snapshot.state.value,
        str(snapshot.model_dump(mode="json")["updated_at"]),
        hashlib.sha256(payload.encode("ascii")).hexdigest(),
        payload,
    )


def _release_row(release: KrLoopReleaseEvent) -> _ReleaseRow:
    payload = canonical_kr_loop_release_json(release)
    return (
        release.release_id,
        release.generation,
        release.action.value,
        release.candidate_id,
        str(release.model_dump(mode="json")["recorded_at"]),
        hashlib.sha256(payload.encode("ascii")).hexdigest(),
        payload,
    )


def _decode_snapshot(row: _SnapshotRow) -> KrLoopCandidateSnapshot:
    value = KrLoopCandidateSnapshot.model_validate_json(row[6])
    if row != _snapshot_row(value):
        raise InvalidKrLoopEngineerStoreError
    return value


def _decode_release(row: _ReleaseRow) -> KrLoopReleaseEvent:
    value = KrLoopReleaseEvent.model_validate_json(row[6])
    if row != _release_row(value):
        raise InvalidKrLoopEngineerStoreError
    return value


def _require_snapshot_chains(values: tuple[KrLoopCandidateSnapshot, ...]) -> None:
    tails: dict[str, KrLoopCandidateSnapshot] = {}
    for value in values:
        previous = tails.get(value.candidate_id)
        if not _transition_allowed(previous, value):
            raise InvalidKrLoopEngineerStoreError
        tails[value.candidate_id] = value


def _require_sha(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidKrLoopEngineerStoreError


__all__ = ("InvalidKrLoopEngineerStoreError", "KrLoopEngineerStore")
