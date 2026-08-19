from __future__ import annotations

from typing import Final

RESEARCH_AGENT_CYCLE_SCHEMA_VERSION: Final = 2

RESEARCH_AGENT_CYCLE_SCHEMA_V1: Final[tuple[str, ...]] = (
    """CREATE TABLE evidence (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_id TEXT UNIQUE NOT NULL,
        agent_family_id TEXT NOT NULL,
        available_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE cycles (
        cycle_id TEXT PRIMARY KEY,
        agent_family_id TEXT NOT NULL,
        evidence_sequence INTEGER NOT NULL REFERENCES evidence(sequence),
        action_request_id TEXT UNIQUE NOT NULL,
        state TEXT NOT NULL,
        started_at TEXT NOT NULL,
        terminal_at TEXT,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE cycle_events (
        event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id TEXT NOT NULL REFERENCES cycles(cycle_id),
        state TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE results (
        result_id TEXT PRIMARY KEY,
        cycle_id TEXT UNIQUE NOT NULL REFERENCES cycles(cycle_id),
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE cursors (
        agent_family_id TEXT PRIMARY KEY,
        evidence_sequence INTEGER NOT NULL
    )""",
    """CREATE TABLE open_work (
        open_work_id TEXT PRIMARY KEY,
        agent_family_id TEXT NOT NULL,
        state TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX evidence_runnable ON evidence(agent_family_id, sequence, available_at)",
    "CREATE INDEX cycles_latest ON cycles(agent_family_id, evidence_sequence DESC)",
    """CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER cycle_events_no_update BEFORE UPDATE ON cycle_events
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER cycle_events_no_delete BEFORE DELETE ON cycle_events
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER results_no_update BEFORE UPDATE ON results
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER results_no_delete BEFORE DELETE ON results
        BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
)

RESEARCH_AGENT_CYCLE_SCHEMA_V2: Final[tuple[str, ...]] = (
    """CREATE TABLE day_cursors (
        agent_family_id TEXT NOT NULL,
        market_id TEXT NOT NULL,
        evidence_sequence INTEGER NOT NULL,
        PRIMARY KEY(agent_family_id,market_id)
    )""",
    "CREATE INDEX day_cursors_sequence ON day_cursors(agent_family_id,market_id,evidence_sequence)",
)

RESEARCH_AGENT_CYCLE_SCHEMA: Final[tuple[str, ...]] = (
    *RESEARCH_AGENT_CYCLE_SCHEMA_V1,
    *RESEARCH_AGENT_CYCLE_SCHEMA_V2,
)

__all__ = (
    "RESEARCH_AGENT_CYCLE_SCHEMA",
    "RESEARCH_AGENT_CYCLE_SCHEMA_V1",
    "RESEARCH_AGENT_CYCLE_SCHEMA_V2",
    "RESEARCH_AGENT_CYCLE_SCHEMA_VERSION",
)
