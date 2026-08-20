from __future__ import annotations

import sqlite3

from pydantic import ValidationError

from trading_agent.day_research_ledger import (
    DayResearchLedgerConflictError,
    InvalidDayResearchLedgerSourceError,
)
from trading_agent.day_research_review_models import ExecutionEligibility, PromotionDecision
from trading_agent.day_research_review_types import (
    DayExecutionEligibilityStatus,
    day_review_content_id,
    required_day_execution_authority_class,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


def record_promotion_decision(
    connection: sqlite3.Connection,
    decision: PromotionDecision,
) -> bool:
    checked = PromotionDecision.model_validate(decision)
    _require_capsule_lineage(
        connection,
        checked.payload.capsule_id,
        checked.payload.hypothesis_version_id,
        checked.payload.market_id.value,
    )
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_promotion_decisions WHERE decision_id=?",
        (checked.decision_id,),
    ).fetchone()
    if row is not None:
        if _stored_decision(row[0]) == checked:
            return False
        raise DayResearchLedgerConflictError("day_promotion_decision_identity_conflict")
    try:
        connection.execute(
            "INSERT INTO day_promotion_decisions VALUES (?,?,?,?,?,?)",
            (
                checked.decision_id,
                checked.payload.capsule_id,
                checked.payload.market_id.value,
                checked.payload.effective_after_session.isoformat(),
                checked.payload.decided_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError("day_promotion_decision_identity_conflict") from error
    return True


def append_execution_eligibility(
    connection: sqlite3.Connection,
    eligibility: ExecutionEligibility,
) -> bool:
    checked = ExecutionEligibility.model_validate(eligibility)
    payload = checked.payload
    _require_capsule_lineage(
        connection,
        payload.capsule_id,
        payload.hypothesis_version_id,
        payload.market_id.value,
    )
    decision = _required_decision(connection, payload.decision_id)
    if (
        decision.payload.capsule_id != payload.capsule_id
        or decision.payload.hypothesis_version_id != payload.hypothesis_version_id
        or decision.payload.market_id is not payload.market_id
    ):
        raise InvalidDayResearchLedgerSourceError("day_execution_decision_lineage_mismatch")
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_execution_eligibility_events WHERE eligibility_event_id=?",
        (checked.eligibility_event_id,),
    ).fetchone()
    if row is not None:
        if _stored_eligibility(row[0]) == checked:
            return False
        raise DayResearchLedgerConflictError("day_execution_eligibility_identity_conflict")
    _require_eligibility_chain(connection, checked)
    if payload.status is DayExecutionEligibilityStatus.ELIGIBLE:
        authority = payload.authority_event
        expected_authority = required_day_execution_authority_class(decision.payload.status)
        if authority is None or authority.payload.decision_id != decision.decision_id:
            raise InvalidDayResearchLedgerSourceError("day_execution_owner_authority_missing")
        if expected_authority is None or authority.payload.authority_class is not expected_authority:
            raise InvalidDayResearchLedgerSourceError("day_execution_owner_authority_class_mismatch")
    try:
        connection.execute(
            "INSERT INTO day_execution_eligibility_events VALUES (?,?,?,?,?,?,?)",
            (
                checked.eligibility_event_id,
                payload.capsule_id,
                payload.market_id.value,
                payload.session_date.isoformat(),
                payload.sequence,
                payload.effective_at.isoformat(),
                canonical_experiment_ledger_json(checked),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DayResearchLedgerConflictError("day_execution_eligibility_identity_conflict") from error
    return True


def _require_capsule_lineage(
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
        raise InvalidDayResearchLedgerSourceError("day_review_capsule_lineage_mismatch")


def _required_decision(
    connection: sqlite3.Connection,
    decision_id: str,
) -> PromotionDecision:
    row: tuple[str] | None = connection.execute(
        "SELECT payload_json FROM day_promotion_decisions WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    if row is None:
        raise InvalidDayResearchLedgerSourceError("day_execution_promotion_decision_missing")
    return _stored_decision(row[0])


def _require_eligibility_chain(
    connection: sqlite3.Connection,
    eligibility: ExecutionEligibility,
) -> None:
    payload = eligibility.payload
    row: tuple[str, int] | None = connection.execute(
        "SELECT eligibility_event_id,sequence FROM day_execution_eligibility_events "
        "WHERE capsule_id=? AND market_id=? ORDER BY sequence DESC LIMIT 1",
        (payload.capsule_id, payload.market_id.value),
    ).fetchone()
    expected_sequence = 1 if row is None else row[1] + 1
    expected_previous = None if row is None else row[0]
    if payload.sequence != expected_sequence or payload.previous_event_id != expected_previous:
        raise DayResearchLedgerConflictError("day_execution_eligibility_chain_conflict")


def _stored_decision(payload_json: str) -> PromotionDecision:
    try:
        return PromotionDecision.model_validate_json(payload_json)
    except ValidationError as error:
        raise InvalidDayResearchLedgerSourceError("stored_day_promotion_decision_invalid") from error


def _stored_eligibility(payload_json: str) -> ExecutionEligibility:
    try:
        stored = ExecutionEligibility.model_validate_json(payload_json)
    except ValidationError as error:
        raise InvalidDayResearchLedgerSourceError("stored_day_execution_eligibility_invalid") from error
    if stored.eligibility_event_id != day_review_content_id(stored.payload):
        raise InvalidDayResearchLedgerSourceError("stored_day_execution_eligibility_identity_invalid")
    return stored


__all__ = ("append_execution_eligibility", "record_promotion_decision")
