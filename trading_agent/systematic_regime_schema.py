from __future__ import annotations

from typing import Final

SYSTEMATIC_REGIME_SCHEMA_V1: Final = (
    "CREATE TABLE systematic_cards (card_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);"
    "CREATE TABLE systematic_card_publications ("
    "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
    "CREATE TABLE systematic_outcomes ("
    "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
    "CREATE TRIGGER systematic_cards_no_update BEFORE UPDATE ON systematic_cards "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_cards_no_delete BEFORE DELETE ON systematic_cards "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_publications_no_update BEFORE UPDATE "
    "ON systematic_card_publications BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_publications_no_delete BEFORE DELETE "
    "ON systematic_card_publications BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_outcomes_no_update BEFORE UPDATE ON systematic_outcomes "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_outcomes_no_delete BEFORE DELETE ON systematic_outcomes "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
)

SYSTEMATIC_REGIME_EXPIRATION_SCHEMA_V2: Final = (
    "CREATE TABLE systematic_card_expirations ("
    "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
    "CREATE TRIGGER systematic_card_expirations_no_update BEFORE UPDATE "
    "ON systematic_card_expirations BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_expirations_no_delete BEFORE DELETE "
    "ON systematic_card_expirations BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_publications_exclude_expired BEFORE INSERT "
    "ON systematic_card_publications WHEN EXISTS ("
    "SELECT 1 FROM systematic_card_expirations WHERE card_id = NEW.card_id) "
    "BEGIN SELECT RAISE(ABORT, 'terminal-card-state'); END;"
    "CREATE TRIGGER systematic_card_expirations_exclude_published BEFORE INSERT "
    "ON systematic_card_expirations WHEN EXISTS ("
    "SELECT 1 FROM systematic_card_publications WHERE card_id = NEW.card_id) "
    "BEGIN SELECT RAISE(ABORT, 'terminal-card-state'); END;"
)

SYSTEMATIC_REGIME_SCHEMA_V2: Final = (
    SYSTEMATIC_REGIME_SCHEMA_V1 + SYSTEMATIC_REGIME_EXPIRATION_SCHEMA_V2
)

__all__ = (
    "SYSTEMATIC_REGIME_EXPIRATION_SCHEMA_V2",
    "SYSTEMATIC_REGIME_SCHEMA_V1",
    "SYSTEMATIC_REGIME_SCHEMA_V2",
)
