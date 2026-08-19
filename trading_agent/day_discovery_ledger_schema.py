from __future__ import annotations

from typing import Final

_TABLES: Final = (
    "day_discovery_budget_accounts",
    "day_discovery_cycles",
    "day_discovery_budget_debits",
    "day_discovery_events",
)

_INDEXES: Final = (
    "day_discovery_cycles_by_account",
    "day_discovery_debits_by_account_cycle",
    "day_discovery_events_by_cycle_sequence",
)

CREATE_DAY_DISCOVERY_LEDGER_SCHEMA_V11: Final = """
CREATE TABLE day_discovery_budget_accounts (
  account_id TEXT PRIMARY KEY
    CHECK(length(account_id)=64 AND account_id NOT GLOB '*[^0-9a-f]*'),
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  budget_epoch_ref TEXT NOT NULL,
  debit_limit INTEGER NOT NULL CHECK(debit_limit>=1),
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(market_id,budget_epoch_ref)
);
CREATE TABLE day_discovery_cycles (
  cycle_id TEXT PRIMARY KEY
    CHECK(length(cycle_id)=64 AND cycle_id NOT GLOB '*[^0-9a-f]*'),
  account_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  evidence_sha256 TEXT NOT NULL
    CHECK(length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
  cursor_sha256 TEXT NOT NULL
    CHECK(length(cursor_sha256)=64 AND cursor_sha256 NOT GLOB '*[^0-9a-f]*'),
  opened_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(account_id,evidence_sha256,cursor_sha256),
  FOREIGN KEY(account_id) REFERENCES day_discovery_budget_accounts(account_id)
);
CREATE INDEX day_discovery_cycles_by_account
ON day_discovery_cycles(account_id,opened_at,cycle_id);

CREATE TABLE day_discovery_budget_debits (
  debit_id TEXT PRIMARY KEY
    CHECK(length(debit_id)=64 AND debit_id NOT GLOB '*[^0-9a-f]*'),
  account_id TEXT NOT NULL,
  cycle_id TEXT NOT NULL,
  branch_index INTEGER NOT NULL CHECK(branch_index BETWEEN 0 AND 2),
  debit_kind TEXT NOT NULL CHECK(debit_kind IN ('call_reservation','cartesian_top_up')),
  amount INTEGER NOT NULL CHECK(amount>=1),
  debited_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(cycle_id,branch_index,debit_kind),
  FOREIGN KEY(account_id) REFERENCES day_discovery_budget_accounts(account_id),
  FOREIGN KEY(cycle_id) REFERENCES day_discovery_cycles(cycle_id)
);
CREATE INDEX day_discovery_debits_by_account_cycle
ON day_discovery_budget_debits(account_id,cycle_id,branch_index,debit_kind);

CREATE TABLE day_discovery_events (
  event_id TEXT PRIMARY KEY
    CHECK(length(event_id)=64 AND event_id NOT GLOB '*[^0-9a-f]*'),
  cycle_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence>=1),
  previous_event_id TEXT,
  branch_index INTEGER CHECK(branch_index BETWEEN 0 AND 2),
  event_kind TEXT NOT NULL CHECK(event_kind IN (
    'cycle_opened','call_reserved','call_response_recorded','branch_prepared',
    'resolution_intent','artifact_verified','artifact_failed','artifact_outcome_unknown',
    'preflight_intent','preflight_verified','preflight_failed','preflight_outcome_unknown',
    'branch_finalized','cycle_finalized'
  )),
  event_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(cycle_id,sequence),
  FOREIGN KEY(cycle_id) REFERENCES day_discovery_cycles(cycle_id),
  FOREIGN KEY(previous_event_id) REFERENCES day_discovery_events(event_id)
);
CREATE INDEX day_discovery_events_by_cycle_sequence
ON day_discovery_events(cycle_id,sequence,event_kind);
""" + "\n".join(
    f"CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    for table in _TABLES
    for action in ("update", "delete")
)

DAY_DISCOVERY_SCHEMA_OBJECTS: Final = frozenset(
    set(_TABLES)
    | set(_INDEXES)
    | {f"{table}_no_{action}" for table in _TABLES for action in ("update", "delete")}
)

__all__ = (
    "CREATE_DAY_DISCOVERY_LEDGER_SCHEMA_V11",
    "DAY_DISCOVERY_SCHEMA_OBJECTS",
)
