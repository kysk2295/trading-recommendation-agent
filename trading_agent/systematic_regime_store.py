from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self, final, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_store_sql import (
    InvalidSystematicRegimeSqliteError,
    private_store_exists,
    systematic_reader_connection,
    systematic_writer_connection,
)


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
            raise InvalidSystematicRegimeStoreError
        return self

    @property
    def artifact_sha256(self) -> str:
        return hashlib.sha256(_canonical_payload(self).encode()).hexdigest()


class SystematicRegimeStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    @contextmanager
    def writer(self) -> Iterator[SystematicRegimeWriter]:
        try:
            with systematic_writer_connection(self.path) as connection:
                yield SystematicRegimeWriter(connection)
        except InvalidSystematicRegimeSqliteError:
            raise InvalidSystematicRegimeStoreError from None

    def cards(self) -> tuple[SystematicRecommendationCard, ...]:
        try:
            if not private_store_exists(self.path):
                return ()
            with systematic_reader_connection(self.path) as connection:
                rows: list[tuple[str]] = connection.execute(
                    "SELECT cards.payload_json FROM systematic_cards AS cards "
                    "JOIN systematic_card_publications AS publications USING (card_id) "
                    "ORDER BY cards.rowid"
                ).fetchall()
            return tuple(SystematicRecommendationCard.model_validate_json(row[0]) for row in rows)
        except (InvalidSystematicRegimeSqliteError, ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None

    def pending_cards(self) -> tuple[SystematicRecommendationCard, ...]:
        try:
            if not private_store_exists(self.path):
                return ()
            with systematic_reader_connection(self.path) as connection:
                rows: list[tuple[str]] = connection.execute(
                    "SELECT cards.payload_json FROM systematic_cards AS cards "
                    "LEFT JOIN systematic_card_publications AS publications USING (card_id) "
                    "LEFT JOIN systematic_card_expirations AS expirations USING (card_id) "
                    "WHERE publications.card_id IS NULL AND expirations.card_id IS NULL "
                    "ORDER BY cards.rowid"
                ).fetchall()
            return tuple(SystematicRecommendationCard.model_validate_json(row[0]) for row in rows)
        except (InvalidSystematicRegimeSqliteError, ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None

    def expired_cards(self) -> tuple[SystematicRecommendationCard, ...]:
        try:
            if not private_store_exists(self.path):
                return ()
            with systematic_reader_connection(self.path) as connection:
                rows: list[tuple[str]] = connection.execute(
                    "SELECT cards.payload_json FROM systematic_cards AS cards "
                    "JOIN systematic_card_expirations AS expirations USING (card_id) "
                    "ORDER BY cards.rowid"
                ).fetchall()
            return tuple(SystematicRecommendationCard.model_validate_json(row[0]) for row in rows)
        except (InvalidSystematicRegimeSqliteError, ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None

    def outcomes(self) -> tuple[SystematicShadowOutcome, ...]:
        try:
            if not private_store_exists(self.path):
                return ()
            with systematic_reader_connection(self.path) as connection:
                rows: list[tuple[str]] = connection.execute(
                    "SELECT payload_json FROM systematic_outcomes ORDER BY rowid"
                ).fetchall()
            return tuple(SystematicShadowOutcome.model_validate_json(row[0]) for row in rows)
        except (InvalidSystematicRegimeSqliteError, ValidationError, ValueError):
            raise InvalidSystematicRegimeStoreError from None


@final
class SystematicRegimeWriter:
    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append_card(self, card: SystematicRecommendationCard) -> bool:
        staged = self.stage_card(card)
        published = self.publish_card(card)
        return staged or published

    def stage_card(self, card: SystematicRecommendationCard) -> bool:
        checked = SystematicRecommendationCard.model_validate(card.model_dump(mode="python"))
        return self._append("systematic_cards", "card_id", checked.card_id, _canonical_payload(checked))

    def publish_card(self, card: SystematicRecommendationCard) -> bool:
        checked = SystematicRecommendationCard.model_validate(card.model_dump(mode="python"))
        return self._append(
            "systematic_card_publications",
            "card_id",
            checked.card_id,
            _canonical_payload(checked),
        )

    def expire_card(self, card: SystematicRecommendationCard) -> bool:
        checked = SystematicRecommendationCard.model_validate(card.model_dump(mode="python"))
        return self._append(
            "systematic_card_expirations",
            "card_id",
            checked.card_id,
            _canonical_payload(checked),
        )

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
