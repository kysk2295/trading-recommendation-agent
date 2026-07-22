from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self, final, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.systematic_regime_models import SystematicRecommendationCard


class InvalidSystematicRegimeStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime store is invalid"


class SystematicRegimeConflictError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime identity has conflicting content"


class SystematicShadowOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    card_id: str
    target_session: dt.date
    observed_at: dt.datetime
    candidate_symbols: tuple[str, ...]
    no_position: bool
    net_return_bps: Decimal | None
    source_key: str

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        has_position = not self.no_position
        if (
            not self.card_id
            or not _aware(self.observed_at)
            or self.candidate_symbols != tuple(sorted(set(self.candidate_symbols)))
            or has_position != (len(self.candidate_symbols) == 2)
            or has_position != (self.net_return_bps is not None)
            or (self.net_return_bps is not None and not self.net_return_bps.is_finite())
            or len(self.source_key) != 64
        ):
            raise ValueError("invalid systematic shadow outcome")
        return self

    @property
    def artifact_sha256(self) -> str:
        return hashlib.sha256(_canonical_payload(self).encode()).hexdigest()


class SystematicRegimeStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    @contextmanager
    def writer(self) -> Iterator[SystematicRegimeWriter]:
        if self.path.is_symlink():
            raise InvalidSystematicRegimeStoreError
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None or lock_path.is_symlink():
            raise InvalidSystematicRegimeStoreError
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError as error:
            raise InvalidSystematicRegimeStoreError from error
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise InvalidSystematicRegimeStoreError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare(connection)
                yield SystematicRegimeWriter(connection)
            finally:
                connection.close()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def cards(self) -> tuple[SystematicRecommendationCard, ...]:
        if not self.path.is_file():
            return ()
        with self._reader() as connection:
            rows: list[tuple[str]] = connection.execute(
                "SELECT payload_json FROM systematic_cards ORDER BY rowid"
            ).fetchall()
        try:
            return tuple(SystematicRecommendationCard.model_validate_json(row[0]) for row in rows)
        except (ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None

    def outcomes(self) -> tuple[SystematicShadowOutcome, ...]:
        if not self.path.is_file():
            return ()
        with self._reader() as connection:
            rows: list[tuple[str]] = connection.execute(
                "SELECT payload_json FROM systematic_outcomes ORDER BY rowid"
            ).fetchall()
        try:
            return tuple(SystematicShadowOutcome.model_validate_json(row[0]) for row in rows)
        except (ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None

    def _reader(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _require_schema(connection)
        return connection


@final
class SystematicRegimeWriter:
    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append_card(self, card: SystematicRecommendationCard) -> bool:
        checked = SystematicRecommendationCard.model_validate(card.model_dump(mode="python"))
        return self._append("systematic_cards", "card_id", checked.card_id, _canonical_payload(checked))

    def append_outcome(self, outcome: SystematicShadowOutcome) -> bool:
        checked = SystematicShadowOutcome.model_validate(outcome.model_dump(mode="python"))
        return self._append("systematic_outcomes", "card_id", checked.card_id, _canonical_payload(checked))

    def _append(self, table: str, identity_column: str, identity: str, payload: str) -> bool:
        existing = self._connection.execute(
            f"SELECT payload_json FROM {table} WHERE {identity_column} = ?",
            (identity,),
        ).fetchone()
        if existing is not None:
            if existing[0] != payload:
                raise SystematicRegimeConflictError
            return False
        try:
            with self._connection:
                _ = self._connection.execute(
                    f"INSERT INTO {table} ({identity_column}, payload_json) VALUES (?, ?)",
                    (identity, payload),
                )
        except sqlite3.IntegrityError as error:
            raise SystematicRegimeConflictError from error
        return True


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(
            "CREATE TABLE systematic_cards (card_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);"
            "CREATE TABLE systematic_outcomes ("
            "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
            "CREATE TRIGGER systematic_cards_no_update BEFORE UPDATE ON systematic_cards "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
            "CREATE TRIGGER systematic_cards_no_delete BEFORE DELETE ON systematic_cards "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
            "CREATE TRIGGER systematic_outcomes_no_update BEFORE UPDATE ON systematic_outcomes "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
            "CREATE TRIGGER systematic_outcomes_no_delete BEFORE DELETE ON systematic_outcomes "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
            "PRAGMA user_version = 1;"
        )
    else:
        _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] = connection.execute("PRAGMA user_version").fetchone()
    if version != (1,):
        raise InvalidSystematicRegimeStoreError


def _canonical_payload(value: BaseModel) -> str:
    return json.dumps(value.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidSystematicRegimeStoreError",
    "SystematicRegimeConflictError",
    "SystematicRegimeStore",
    "SystematicShadowOutcome",
)
