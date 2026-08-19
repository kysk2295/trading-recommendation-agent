from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Self

from pydantic import Field, model_validator

from trading_agent.strategy_research_ledger import (
    AgentResearchStateEvent,
    HoldoutReveal,
    StrategyResearchLedgerError,
)
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import CanonicalModel, ResearchAgentId, aware


class SanitizedHoldoutReveal(CanonicalModel):
    reveal_id: str = Field(min_length=1)
    sanitized_result: TerminalResearchResult
    revealed_at: dt.datetime

    @model_validator(mode="after")
    def validate_reveal_time(self) -> Self:
        if not aware(self.revealed_at) or self.revealed_at < self.sanitized_result.evaluated_at:
            raise StrategyResearchLedgerError("sanitized_reveal_time_invalid")
        return self


def read_preregistrations(connection: sqlite3.Connection) -> tuple[PreregistrationManifest, ...]:
    rows = connection.execute("SELECT payload_json FROM strategy_research_preregistrations ORDER BY rowid").fetchall()
    return tuple(PreregistrationManifest.model_validate_json(row[0]) for row in rows)


def read_attempts(connection: sqlite3.Connection, hypothesis_id: str) -> tuple[ResearchAttempt, ...]:
    rows = connection.execute(
        "SELECT payload_json FROM strategy_research_attempts WHERE hypothesis_id=? ORDER BY branch_index",
        (hypothesis_id,),
    ).fetchall()
    return tuple(ResearchAttempt.model_validate_json(row[0]) for row in rows)


def read_feedback(connection: sqlite3.Connection, agent_id: ResearchAgentId) -> tuple[TerminalResearchResult, ...]:
    rows = connection.execute(
        "SELECT sanitized_payload_json FROM strategy_research_holdout_reveals WHERE owner_agent_id=? ORDER BY rowid",
        (agent_id.value,),
    ).fetchall()
    return tuple(TerminalResearchResult.model_validate_json(row[0]) for row in rows)


def read_agent_state(connection: sqlite3.Connection, agent_id: ResearchAgentId) -> tuple[AgentResearchStateEvent, ...]:
    rows = connection.execute(
        "SELECT payload_json FROM strategy_research_agent_state_events WHERE agent_id=? ORDER BY sequence",
        (agent_id.value,),
    ).fetchall()
    return tuple(AgentResearchStateEvent.model_validate_json(row[0]) for row in rows)


def read_agent_state_event(connection: sqlite3.Connection, event_id: str) -> AgentResearchStateEvent | None:
    row = connection.execute(
        "SELECT payload_json FROM strategy_research_agent_state_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return None if row is None else AgentResearchStateEvent.model_validate_json(row[0])


def read_sanitized_reveals(
    connection: sqlite3.Connection,
    agent_id: ResearchAgentId,
) -> tuple[SanitizedHoldoutReveal, ...]:
    rows = connection.execute(
        "SELECT exact_payload_json FROM strategy_research_holdout_reveals WHERE owner_agent_id=? ORDER BY rowid",
        (agent_id.value,),
    ).fetchall()
    reveals = tuple(HoldoutReveal.model_validate_json(row[0]) for row in rows)
    return tuple(
        SanitizedHoldoutReveal(
            reveal_id=reveal.reveal_id,
            sanitized_result=reveal.sanitized_result,
            revealed_at=reveal.revealed_at,
        )
        for reveal in reveals
    )
