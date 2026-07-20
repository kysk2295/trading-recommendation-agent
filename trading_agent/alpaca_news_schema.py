from __future__ import annotations

from typing import Final

ALPACA_NEWS_SCHEMA_VERSION: Final = 1
ALPACA_NEWS_SCHEMA: Final = """
CREATE TABLE alpaca_news_receipts (
  receipt_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  request_payload_sha256 TEXT NOT NULL,
  request_payload_json TEXT NOT NULL,
  page_index INTEGER NOT NULL,
  page_token TEXT,
  received_at TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  content_encoding TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  UNIQUE(request_id, page_index)
);
CREATE TABLE alpaca_news_runs (
  run_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TRIGGER alpaca_news_receipts_no_update
BEFORE UPDATE ON alpaca_news_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_news_receipts_no_delete
BEFORE DELETE ON alpaca_news_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_news_runs_no_update
BEFORE UPDATE ON alpaca_news_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_news_runs_no_delete
BEFORE DELETE ON alpaca_news_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
ALPACA_NEWS_SCHEMA_OBJECTS: Final = frozenset(
    {
        "alpaca_news_receipts",
        "alpaca_news_runs",
        "alpaca_news_receipts_no_update",
        "alpaca_news_receipts_no_delete",
        "alpaca_news_runs_no_update",
        "alpaca_news_runs_no_delete",
    }
)
