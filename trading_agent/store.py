from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Final, final

from trading_agent.models import (
    Recommendation,
    RecommendationAlert,
    RecommendationEvent,
    RecommendationState,
)

RecommendationRow = tuple[str, str, str, str, float, float, float, float, str, str]
EventRow = tuple[str, str, str, float | None, str]
AlertRow = tuple[str, str, str, str]
CREATE_RECOMMENDATIONS: Final = """CREATE TABLE IF NOT EXISTS recommendations (
recommendation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
strategy TEXT NOT NULL, created_at TEXT NOT NULL, entry REAL NOT NULL,
stop REAL NOT NULL, target_1r REAL NOT NULL, target_2r REAL NOT NULL,
state TEXT NOT NULL, rationale TEXT NOT NULL)"""
CREATE_EVENTS: Final = """CREATE TABLE IF NOT EXISTS events (
event_id INTEGER PRIMARY KEY AUTOINCREMENT, recommendation_id TEXT NOT NULL,
occurred_at TEXT NOT NULL, state TEXT NOT NULL, price REAL, note TEXT NOT NULL)"""
CREATE_CHECKPOINTS: Final = """CREATE TABLE IF NOT EXISTS bar_checkpoints (
symbol TEXT PRIMARY KEY, processed_at TEXT NOT NULL, last_close REAL NOT NULL)"""
CREATE_ALERTS: Final = """CREATE TABLE IF NOT EXISTS alert_outbox (
recommendation_id TEXT PRIMARY KEY, queued_at TEXT NOT NULL,
payload_json TEXT NOT NULL, card_markdown TEXT NOT NULL)"""
INSERT_EVENT: Final = """INSERT INTO events
(recommendation_id, occurred_at, state, price, note) VALUES (?, ?, ?, ?, ?)"""
SELECT_EVENTS: Final = """SELECT recommendation_id, occurred_at, state, price, note
FROM events WHERE recommendation_id = ? ORDER BY event_id"""
UPSERT_CHECKPOINT: Final = """INSERT INTO bar_checkpoints
(symbol, processed_at, last_close) VALUES (?, ?, ?)
ON CONFLICT(symbol) DO UPDATE SET processed_at = excluded.processed_at,
last_close = excluded.last_close"""
INSERT_ALERT: Final = """INSERT OR IGNORE INTO alert_outbox
(recommendation_id, queued_at, payload_json, card_markdown) VALUES (?, ?, ?, ?)"""


@final
class PaperStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            _ = connection.execute(CREATE_RECOMMENDATIONS)
            _ = connection.execute(CREATE_EVENTS)
            _ = connection.execute(CREATE_CHECKPOINTS)
            _ = connection.execute(CREATE_ALERTS)
            checkpoint_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(bar_checkpoints)")
            }
            if "last_close" not in checkpoint_columns:
                _ = connection.execute(
                    "ALTER TABLE bar_checkpoints ADD COLUMN last_close REAL"
                )

    def save(self, recommendation: Recommendation) -> None:
        with self._connect() as connection:
            _ = connection.execute(
                "INSERT INTO recommendations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recommendation.recommendation_id,
                    recommendation.symbol,
                    recommendation.strategy,
                    recommendation.created_at.isoformat(),
                    recommendation.entry,
                    recommendation.stop,
                    recommendation.target_1r,
                    recommendation.target_2r,
                    recommendation.state.value,
                    recommendation.rationale,
                ),
            )
        self.set_state(
            recommendation.recommendation_id,
            recommendation.state,
            recommendation.created_at,
            None,
            "추천 생성",
        )

    def set_state(
        self,
        recommendation_id: str,
        state: RecommendationState,
        occurred_at: dt.datetime,
        price: float | None,
        note: str,
    ) -> None:
        with self._connect() as connection:
            _ = connection.execute(
                "UPDATE recommendations SET state = ? WHERE recommendation_id = ?",
                (state.value, recommendation_id),
            )
            _ = connection.execute(
                INSERT_EVENT,
                (recommendation_id, occurred_at.isoformat(), state.value, price, note),
            )

    def recommendations(self) -> tuple[Recommendation, ...]:
        with self._connect() as connection:
            rows: list[RecommendationRow] = connection.execute(
                "SELECT * FROM recommendations ORDER BY created_at, symbol"
            ).fetchall()
        return tuple(_recommendation_from_row(row) for row in rows)

    def open_recommendations(self, symbol: str) -> tuple[Recommendation, ...]:
        return tuple(
            row
            for row in self.recommendations()
            if row.symbol == symbol
            and row.state
            in {
                RecommendationState.SETUP,
                RecommendationState.ACTIVE,
                RecommendationState.TARGET_1R,
            }
        )

    def events(self, recommendation_id: str) -> tuple[RecommendationEvent, ...]:
        with self._connect() as connection:
            rows: list[EventRow] = connection.execute(
                SELECT_EVENTS,
                (recommendation_id,),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def last_processed_bar(self, symbol: str) -> dt.datetime | None:
        with self._connect() as connection:
            row: tuple[str] | None = connection.execute(
                "SELECT processed_at FROM bar_checkpoints WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return None if row is None else dt.datetime.fromisoformat(row[0])

    def last_processed_close(self, symbol: str) -> float | None:
        with self._connect() as connection:
            row: tuple[float | None] | None = connection.execute(
                "SELECT last_close FROM bar_checkpoints WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return None if row is None or row[0] is None else float(row[0])

    def set_last_processed_bar(
        self,
        symbol: str,
        timestamp: dt.datetime,
        close: float,
    ) -> None:
        current = self.last_processed_bar(symbol)
        if current is not None and timestamp <= current:
            return
        with self._connect() as connection:
            _ = connection.execute(
                UPSERT_CHECKPOINT,
                (symbol, timestamp.isoformat(), close),
            )

    def queue_alert(self, alert: RecommendationAlert) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                INSERT_ALERT,
                (
                    alert.recommendation_id,
                    alert.queued_at.isoformat(),
                    alert.payload_json,
                    alert.card_markdown,
                ),
            )
        return cursor.rowcount == 1

    def alerts(self) -> tuple[RecommendationAlert, ...]:
        with self._connect() as connection:
            rows: list[AlertRow] = connection.execute(
                """SELECT recommendation_id, queued_at, payload_json, card_markdown
                FROM alert_outbox ORDER BY queued_at, recommendation_id"""
            ).fetchall()
        return tuple(
            RecommendationAlert(
                row[0],
                dt.datetime.fromisoformat(row[1]),
                row[2],
                row[3],
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _recommendation_from_row(row: RecommendationRow) -> Recommendation:
    return Recommendation(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        dt.datetime.fromisoformat(str(row[3])),
        float(row[4]),
        float(row[5]),
        float(row[6]),
        float(row[7]),
        RecommendationState(str(row[8])),
        str(row[9]),
    )


def _event_from_row(row: EventRow) -> RecommendationEvent:
    return RecommendationEvent(
        str(row[0]),
        dt.datetime.fromisoformat(str(row[1])),
        RecommendationState(str(row[2])),
        None if row[3] is None else float(row[3]),
        str(row[4]),
    )
