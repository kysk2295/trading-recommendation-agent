from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trading_agent.hermes_arm_request import (
    HermesArmFailure,
    HermesArmRequest,
    HermesArmTransition,
    HermesArmTransitionKind,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_signing import HermesArmSigner

_SCHEMA = """
CREATE TABLE IF NOT EXISTS arm_requests (
    request_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    signature TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arm_transitions (
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    signature TEXT NOT NULL,
    PRIMARY KEY (request_id, sequence),
    FOREIGN KEY (request_id) REFERENCES arm_requests(request_id)
);
CREATE TRIGGER IF NOT EXISTS arm_requests_no_update BEFORE UPDATE ON arm_requests
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS arm_requests_no_delete BEFORE DELETE ON arm_requests
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS arm_transitions_no_update BEFORE UPDATE ON arm_transitions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS arm_transitions_no_delete BEFORE DELETE ON arm_transitions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
PRAGMA user_version = 1;
"""


class HermesArmStore:
    __slots__ = ("_signer", "path")

    def __init__(self, path: Path, signer: HermesArmSigner) -> None:
        self.path = path.resolve(strict=False)
        self._signer = signer

    def add_request(self, request: HermesArmRequest) -> None:
        payload = _request_payload(request)
        if not self._signer.verify(payload, request.signature):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        with self._writer() as connection:
            try:
                connection.execute(
                    "INSERT INTO arm_requests(request_id, payload_json, signature) VALUES (?, ?, ?)",
                    (request.request_id, payload, request.signature),
                )
            except sqlite3.IntegrityError:
                raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE) from None

    def request(self, request_id: str) -> HermesArmRequest:
        self._initialize()
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            row: tuple[str, str] | None = connection.execute(
                "SELECT payload_json, signature FROM arm_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None or not self._signer.verify(row[0], row[1]):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        values = json.loads(row[0])
        values["signature"] = row[1]
        return HermesArmRequest.model_validate_json(json.dumps(values))

    def transitions(self, request_id: str) -> tuple[HermesArmTransition, ...]:
        self._initialize()
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT payload_json, signature FROM arm_transitions WHERE request_id = ? ORDER BY sequence",
                (request_id,),
            ).fetchall()
        transitions: list[HermesArmTransition] = []
        for payload, signature in rows:
            if not self._signer.verify(payload, signature):
                raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
            values = json.loads(payload)
            values["signature"] = signature
            transitions.append(HermesArmTransition.model_validate_json(json.dumps(values)))
        _require_chain(tuple(transitions))
        return tuple(transitions)

    def append_transition(
        self,
        transition: HermesArmTransition,
        allowed_previous: tuple[HermesArmTransitionKind | None, ...],
    ) -> None:
        payload = _transition_payload(transition)
        if not self._signer.verify(payload, transition.signature):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        with self._writer() as connection:
            row: tuple[int, str, str] | None = connection.execute(
                """SELECT sequence, kind, signature FROM arm_transitions
                WHERE request_id = ? ORDER BY sequence DESC LIMIT 1""",
                (transition.request_id,),
            ).fetchone()
            previous = None if row is None else HermesArmTransitionKind(row[1])
            sequence = 1 if row is None else row[0] + 1
            previous_signature = None if row is None else row[2]
            invalid_transition = (
                previous not in allowed_previous
                or transition.sequence != sequence
                or transition.previous_signature != previous_signature
            )
            if invalid_transition:
                raise InvalidHermesArmRequestError(_transition_conflict(previous))
            connection.execute(
                """INSERT INTO arm_transitions(request_id, sequence, kind, payload_json, signature)
                VALUES (?, ?, ?, ?, ?)""",
                (transition.request_id, transition.sequence, transition.kind.value, payload, transition.signature),
            )

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(_SCHEMA)
        os.chmod(self.path, 0o600)

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(Path(f"{self.path}.writer.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE) from None
            connection = sqlite3.connect(self.path, timeout=0.0)
            try:
                connection.executescript(_SCHEMA)
                _ = connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
                os.chmod(self.path, 0o600)
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _request_payload(request: HermesArmRequest) -> str:
    return json.dumps(request.model_dump(mode="json", exclude={"signature"}), separators=(",", ":"), sort_keys=True)


def _transition_payload(transition: HermesArmTransition) -> str:
    return json.dumps(
        transition.model_dump(mode="json", exclude={"signature"}), separators=(",", ":"), sort_keys=True
    )


def _require_chain(transitions: tuple[HermesArmTransition, ...]) -> None:
    previous: str | None = None
    for sequence, transition in enumerate(transitions, start=1):
        if transition.sequence != sequence or transition.previous_signature != previous:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        previous = transition.signature


def _transition_conflict(previous: HermesArmTransitionKind | None) -> HermesArmFailure:
    if previous is HermesArmTransitionKind.CONFIRMED:
        return HermesArmFailure.CONFIRMATION_REPLAYED
    if previous is HermesArmTransitionKind.CONSUMED:
        return HermesArmFailure.CONSUMED
    if previous is HermesArmTransitionKind.REVOKED:
        return HermesArmFailure.REVOKED
    if previous is HermesArmTransitionKind.EXPIRED:
        return HermesArmFailure.EXPIRED
    return HermesArmFailure.NOT_CONFIRMED
