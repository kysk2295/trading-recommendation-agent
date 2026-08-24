from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_projection import (
    InvalidKrDayDecisionProjectionError,
    KrDayDecisionProjection,
    project_kr_day_decision,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore

_SEOUL: Final = ZoneInfo("Asia/Seoul")


class InvalidKrDayDecisionServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day decision service input is invalid"


@dataclass(frozen=True, slots=True)
class _BatchItem:
    raw: KrDayCapsuleEvaluationRequest
    validated: KrDayCapsuleEvaluationRequest | None
    capsule_id: str
    opportunity_id: str
    completed_bar_at: dt.datetime
    evaluated_at: dt.datetime


def run_kr_day_decision_tick(
    requests: tuple[KrDayCapsuleEvaluationRequest, ...],
    store: KrDayDecisionStore,
) -> tuple[KrDayDecisionEvent, ...]:
    if len(requests) > 3:
        raise InvalidKrDayDecisionServiceError
    items = tuple(sorted((_batch_item(item) for item in requests), key=lambda item: item.capsule_id))
    if len({item.capsule_id for item in items}) != len(items):
        raise InvalidKrDayDecisionServiceError
    if len({(item.opportunity_id, item.completed_bar_at, item.evaluated_at) for item in items}) > 1:
        raise InvalidKrDayDecisionServiceError
    working = list(store.events())
    results: list[KrDayDecisionEvent] = []
    for item in items:
        event = _decide(item, tuple(working))
        _ = store.append(event)
        if event not in working:
            working.append(event)
        results.append(event)
    return tuple(results)


def _batch_item(raw: KrDayCapsuleEvaluationRequest) -> _BatchItem:
    try:
        capsule_id = raw.capsule.capsule_id
        opportunity_id = raw.opportunity.opportunity_id
        completed_bar_at = raw.bars[-1].end_at
        evaluated_at = raw.evaluated_at
    except (AttributeError, IndexError):
        raise InvalidKrDayDecisionServiceError from None
    try:
        validated = KrDayCapsuleEvaluationRequest.model_validate(raw.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        validated = None
    return _BatchItem(raw, validated, capsule_id, opportunity_id, completed_bar_at, evaluated_at)


def _decide(item: _BatchItem, persisted: tuple[KrDayDecisionEvent, ...]) -> KrDayDecisionEvent:
    input_sha256 = _input_sha256(item.raw)
    session_date = item.completed_bar_at.astimezone(_SEOUL).date()
    same_bar = tuple(
        event
        for event in persisted
        if event.session_date == session_date
        and event.capsule_id == item.capsule_id
        and event.opportunity_id == item.opportunity_id
        and event.completed_bar_at == item.completed_bar_at
    )
    if len(same_bar) > 1:
        raise InvalidKrDayDecisionServiceError
    if same_bar:
        if _evidence_value(same_bar[0], "decision_input_sha256") != input_sha256:
            raise InvalidKrDayDecisionServiceError
        return same_bar[0]
    previous = next(
        (
            event
            for event in reversed(persisted)
            if event.session_date == session_date
            and event.capsule_id == item.capsule_id
            and event.opportunity_id == item.opportunity_id
        ),
        None,
    )
    if item.validated is None:
        return _invalid_event(item, input_sha256, previous)
    active_theses = tuple(
        sorted(
            {
                evidence.value
                for event in persisted
                if event.session_date == session_date
                and event.status is KrDayDecisionStatus.ARMED
                and (event.capsule_id, event.opportunity_id) != (item.capsule_id, item.opportunity_id)
                for evidence in event.observed_evidence
                if evidence.name == "thesis_key"
            }
        )
    )
    try:
        projection = project_kr_day_decision(
            item.validated,
            active_theses,
            previous is not None and previous.status is KrDayDecisionStatus.INVESTIGATING,
        )
    except (InvalidKrDayDecisionProjectionError, ValidationError, ValueError):
        return _invalid_event(item, input_sha256, previous)
    return _event(item.validated, projection, input_sha256, previous)


def _event(
    request: KrDayCapsuleEvaluationRequest,
    projection: KrDayDecisionProjection,
    input_sha256: str,
    previous: KrDayDecisionEvent | None,
) -> KrDayDecisionEvent:
    evidence = tuple(
        sorted(
            (
                *projection.observed_evidence,
                KrDayDecisionEvidenceValue(name="decision_input_sha256", value=input_sha256),
            ),
            key=lambda item: item.name,
        )
    )
    payload = KrDayDecisionEventPayload(
        capsule_id=request.capsule.capsule_id,
        hypothesis_version_id=request.capsule.hypothesis_version_id,
        opportunity_id=request.opportunity.opportunity_id,
        session_date=request.bars[-1].end_at.astimezone(_SEOUL).date(),
        symbol=request.opportunity.candidates[0].symbol,
        completed_bar_at=request.bars[-1].end_at,
        observed_at=request.evaluated_at,
        valid_until=projection.valid_until,
        status=projection.status,
        reason_codes=projection.reason_codes,
        conditional_plan=projection.plan,
        evidence_refs=projection.evidence_refs,
        observed_evidence=evidence,
        previous_event_id=None if previous is None else previous.event_id,
    )
    return KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python") | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )


def _invalid_event(
    item: _BatchItem,
    input_sha256: str,
    previous: KrDayDecisionEvent | None,
) -> KrDayDecisionEvent:
    raw = item.raw
    expired = raw.opportunity.valid_until <= raw.evaluated_at
    status = KrDayDecisionStatus.EXPIRED if expired else KrDayDecisionStatus.BLOCKED
    deadline = (
        max(item.completed_bar_at, min(raw.opportunity.valid_until, raw.evaluated_at))
        if expired
        else min(
            raw.opportunity.valid_until,
            raw.evaluated_at.astimezone(_SEOUL).replace(hour=15, minute=30, second=0, microsecond=0),
        )
    )
    refs = tuple(
        sorted(
            {
                *(evidence.canonical_id for evidence in raw.opportunity.evidence_refs),
                *(evidence.canonical_id for evidence in raw.market.evidence_refs),
                *(bar.evidence_ref.canonical_id for bar in raw.bars),
            }
        )
    )
    projection = KrDayDecisionProjection(
        status,
        (KrDayDecisionReasonCode.POLICY_INPUT_CONTRACT_MISMATCH,),
        None,
        refs,
        (KrDayDecisionEvidenceValue(name="input_valid", value="false"),),
        deadline,
    )
    return _event(raw, projection, input_sha256, previous)


def _input_sha256(request: KrDayCapsuleEvaluationRequest) -> str:
    try:
        return hashlib.sha256(canonical_experiment_ledger_json(request).encode()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        raise InvalidKrDayDecisionServiceError from None


def _evidence_value(event: KrDayDecisionEvent, name: str) -> str | None:
    return next((item.value for item in event.observed_evidence if item.name == name), None)


__all__ = ("InvalidKrDayDecisionServiceError", "run_kr_day_decision_tick")
