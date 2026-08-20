from __future__ import annotations

import sqlite3

from pydantic import ValidationError

from trading_agent.day_research_ledger import InvalidDayResearchLedgerSourceError
from trading_agent.day_research_review_models import ExecutionEligibility, PromotionDecision
from trading_agent.research_identity_models import MarketId


def read_promotion_decisions(
    connection: sqlite3.Connection,
    market_id: MarketId | None = None,
    capsule_id: str | None = None,
) -> tuple[PromotionDecision, ...]:
    rows: list[tuple[str, str, str, str, str, str]] = connection.execute(
        "SELECT decision_id,capsule_id,market_id,effective_session_date,decided_at,payload_json "
        "FROM day_promotion_decisions ORDER BY rowid"
    ).fetchall()
    decisions = tuple(_decision(row) for row in rows)
    for decision in decisions:
        _require_capsule_parent(
            connection,
            decision.payload.capsule_id,
            decision.payload.hypothesis_version_id,
            decision.payload.market_id.value,
        )
    return tuple(
        decision
        for decision in decisions
        if (market_id is None or decision.payload.market_id is market_id)
        and (capsule_id is None or decision.payload.capsule_id == capsule_id)
    )


def read_execution_eligibility_events(
    connection: sqlite3.Connection,
    market_id: MarketId | None = None,
    capsule_id: str | None = None,
) -> tuple[ExecutionEligibility, ...]:
    rows: list[tuple[str, str, str, str, int, str, str]] = connection.execute(
        "SELECT eligibility_event_id,capsule_id,market_id,session_date,sequence,effective_at,payload_json "
        "FROM day_execution_eligibility_events ORDER BY rowid"
    ).fetchall()
    events = tuple(_eligibility(row) for row in rows)
    decisions = {decision.decision_id: decision for decision in read_promotion_decisions(connection)}
    previous_by_lineage: dict[tuple[str, MarketId], ExecutionEligibility] = {}
    for event in events:
        payload = event.payload
        decision = decisions.get(payload.decision_id)
        if decision is None or (
            decision.payload.capsule_id != payload.capsule_id
            or decision.payload.hypothesis_version_id != payload.hypothesis_version_id
            or decision.payload.market_id is not payload.market_id
        ):
            raise InvalidDayResearchLedgerSourceError("stored_day_execution_decision_invalid")
        key = (payload.capsule_id, payload.market_id)
        previous = previous_by_lineage.get(key)
        if payload.sequence != (
            1 if previous is None else previous.payload.sequence + 1
        ) or payload.previous_event_id != (None if previous is None else previous.eligibility_event_id):
            raise InvalidDayResearchLedgerSourceError("stored_day_execution_chain_invalid")
        previous_by_lineage[key] = event
    return tuple(
        event
        for event in events
        if (market_id is None or event.payload.market_id is market_id)
        and (capsule_id is None or event.payload.capsule_id == capsule_id)
    )


def _decision(row: tuple[str, str, str, str, str, str]) -> PromotionDecision:
    try:
        decision = PromotionDecision.model_validate_json(row[5])
    except ValidationError as error:
        raise InvalidDayResearchLedgerSourceError("stored_day_promotion_decision_invalid") from error
    payload = decision.payload
    if (
        row[0] != decision.decision_id
        or row[1] != payload.capsule_id
        or row[2] != payload.market_id.value
        or row[3] != payload.effective_after_session.isoformat()
        or row[4] != payload.decided_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_promotion_projection_invalid")
    return decision


def _eligibility(row: tuple[str, str, str, str, int, str, str]) -> ExecutionEligibility:
    try:
        event = ExecutionEligibility.model_validate_json(row[6])
    except ValidationError as error:
        raise InvalidDayResearchLedgerSourceError("stored_day_execution_eligibility_invalid") from error
    payload = event.payload
    if (
        row[0] != event.eligibility_event_id
        or row[1] != payload.capsule_id
        or row[2] != payload.market_id.value
        or row[3] != payload.session_date.isoformat()
        or row[4] != payload.sequence
        or row[5] != payload.effective_at.isoformat()
    ):
        raise InvalidDayResearchLedgerSourceError("stored_day_execution_projection_invalid")
    return event


def _require_capsule_parent(
    connection: sqlite3.Connection,
    capsule_id: str,
    hypothesis_version_id: str,
    market_id: str,
) -> None:
    row: tuple[str, str] | None = connection.execute(
        "SELECT hypothesis_version_id,market_id FROM day_strategy_capsules WHERE capsule_id=?",
        (capsule_id,),
    ).fetchone()
    if row != (hypothesis_version_id, market_id):
        raise InvalidDayResearchLedgerSourceError("stored_day_review_capsule_invalid")


__all__ = ("read_execution_eligibility_events", "read_promotion_decisions")
