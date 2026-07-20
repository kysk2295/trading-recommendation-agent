from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_theme_day_review_models import KrThemeDayReviewEvent
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_theme_day_reviews (
  event_key TEXT PRIMARY KEY,
  strategy_version TEXT NOT NULL,
  as_of_session TEXT NOT NULL,
  reviewer_version TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(strategy_version, as_of_session, reviewer_version)
);
CREATE INDEX kr_theme_day_reviews_by_strategy
ON kr_theme_day_reviews(strategy_version, as_of_session, reviewer_version);
CREATE TRIGGER kr_theme_day_reviews_no_update
BEFORE UPDATE ON kr_theme_day_reviews BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_reviews_no_delete
BEFORE DELETE ON kr_theme_day_reviews BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_reviews",
        "kr_theme_day_reviews_by_strategy",
        "kr_theme_day_reviews_no_update",
        "kr_theme_day_reviews_no_delete",
    }
)


class InvalidKrThemeDayReviewStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day review store is invalid"


@final
class KrThemeDayReviewStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def events(self) -> tuple[KrThemeDayReviewEvent, ...]:
        if self.path.is_symlink():
            raise InvalidKrThemeDayReviewStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _ = connection.execute("PRAGMA query_only = ON")
                _require_schema(connection)
                rows: list[tuple[str, str, str, str, str, str]] = connection.execute(
                    "SELECT event_key,strategy_version,as_of_session,reviewer_version,payload_sha256,payload_json "
                    "FROM kr_theme_day_reviews ORDER BY as_of_session,rowid"
                ).fetchall()
            return tuple(_event_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayReviewStoreError from None

    def review_event(
        self,
        strategy_version: str,
        as_of_session: str,
        reviewer_version: str,
    ) -> KrThemeDayReviewEvent | None:
        matches = tuple(
            event
            for event in self.events()
            if event.strategy_version == strategy_version
            and event.as_of_session.isoformat() == as_of_session
            and event.reviewer_version == reviewer_version
        )
        if len(matches) > 1:
            raise InvalidKrThemeDayReviewStoreError
        return None if not matches else matches[0]

    def append(self, event: KrThemeDayReviewEvent) -> bool:
        try:
            event = KrThemeDayReviewEvent.model_validate(event.model_dump(mode="python"))
            _ = self.events()
            if self.path.is_symlink():
                raise InvalidKrThemeDayReviewStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(event)
                existing: tuple[str, str, str, str, str, str] | None = connection.execute(
                    "SELECT event_key,strategy_version,as_of_session,reviewer_version,payload_sha256,payload_json "
                    "FROM kr_theme_day_reviews WHERE strategy_version=? AND as_of_session=? AND reviewer_version=?",
                    (event.strategy_version, event.as_of_session.isoformat(), event.reviewer_version),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrThemeDayReviewStoreError
                    connection.rollback()
                    return False
                _ = connection.execute("INSERT INTO kr_theme_day_reviews VALUES (?,?,?,?,?,?)", row)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayReviewStoreError from None


def _row(event: KrThemeDayReviewEvent) -> tuple[str, str, str, str, str, str]:
    payload = canonical_experiment_ledger_json(event)
    event_key = kr_theme_day_review_event_key(event)
    return (
        event_key,
        event.strategy_version,
        event.as_of_session.isoformat(),
        event.reviewer_version,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def kr_theme_day_review_event_key(event: KrThemeDayReviewEvent) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(event).encode()).hexdigest()


def _event_from_row(row: tuple[str, str, str, str, str, str]) -> KrThemeDayReviewEvent:
    key, strategy_version, as_of_session, reviewer_version, payload_sha, payload = row
    event = KrThemeDayReviewEvent.model_validate_json(payload)
    if (
        row != _row(event)
        or key != hashlib.sha256(payload.encode()).hexdigest()
        or strategy_version != event.strategy_version
        or as_of_session != event.as_of_session.isoformat()
        or reviewer_version != event.reviewer_version
        or payload_sha != hashlib.sha256(payload.encode()).hexdigest()
    ):
        raise InvalidKrThemeDayReviewStoreError
    return event


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrThemeDayReviewStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKrThemeDayReviewStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDayReviewStoreError
