from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, override

from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore
from trading_agent.us_day_operating_models import UsDayOperatingRequest, UsDayOperatingResult, UsDayOperatingTransition
from trading_agent.us_day_signal_admission import UsDaySignalAdmissionRequest, admit_us_day_signal
from trading_agent.us_day_thesis_models import ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis
from trading_agent.us_day_thesis_store import UsDayThesisStore


class UsDayCoordinator(Protocol):
    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult: ...


@dataclass(frozen=True, slots=True)
class InvalidUsDayAgentOperatingError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class UsDayAgentOperatingRequest:
    admission: UsDaySignalAdmissionRequest
    arm_request_id: str
    actionable_payload_sha256: str

    @property
    def thesis(self) -> UsDayTradeThesis:
        return self.admission.thesis


@dataclass(slots=True)
class UsDayAgentOperatingServices:
    """Own replay memory so one service cannot mutate Paper twice for one thesis."""

    coordinator: UsDayCoordinator
    thesis_store: UsDayThesisStore
    paper_store: PaperStore
    _results: dict[str, UsDayOperatingResult] = field(default_factory=dict, init=False, repr=False)


def operate_us_day_agent(
    request: UsDayAgentOperatingRequest,
    services: UsDayAgentOperatingServices,
) -> UsDayOperatingResult:
    source = request.admission
    thesis = source.thesis
    order_admission = admit_us_day_signal(source)
    replay = services._results.get(thesis.thesis_id)
    if replay is not None:
        return replay
    _ = services.thesis_store.publish_thesis(thesis)
    _ensure_compatibility_recommendation(services.paper_store, source)
    result = services.coordinator.run(
        UsDayOperatingRequest(
            arm_request_id=request.arm_request_id,
            session_id=source.session_id,
            strategy_version=source.champion.strategy_version,
            order_admission=order_admission,
            quote_observed_at=source.current_market.quote.observed_at,
            evaluated_at=source.evaluated_at,
            actionable_payload_sha256=request.actionable_payload_sha256,
            lane_id=source.lane_id,
            thesis=thesis,
        )
    )
    _project_acknowledged_transitions(result, source, services)
    services._results[thesis.thesis_id] = result
    return result


def _ensure_compatibility_recommendation(store: PaperStore, request: UsDaySignalAdmissionRequest) -> None:
    thesis = request.thesis
    if thesis.symbol is None or thesis.entry_price is None or thesis.stop_price is None:
        raise InvalidUsDayAgentOperatingError("recommendation_thesis_required")
    recommendation = Recommendation(
        thesis.thesis_id,
        thesis.symbol,
        request.champion.strategy_version,
        thesis.observed_at,
        float(thesis.entry_price),
        float(thesis.stop_price),
        float(thesis.targets[0].price),
        float(thesis.targets[1].price),
        RecommendationState.SETUP,
        thesis.rationale,
    )
    existing = tuple(item for item in store.recommendations() if item.recommendation_id == thesis.thesis_id)
    if existing:
        if len(existing) != 1 or existing[0] != recommendation:
            raise InvalidUsDayAgentOperatingError("compatibility_recommendation_mismatch")
        return
    store.save(recommendation)


def _project_acknowledged_transitions(
    result: UsDayOperatingResult,
    request: UsDaySignalAdmissionRequest,
    services: UsDayAgentOperatingServices,
) -> None:
    acknowledged = tuple(
        transition
        for transition in result.transitions
        if transition
        in {
            UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
            UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
        }
    )
    prior = services.thesis_store.changes(request.thesis.thesis_id)
    expected_notes = tuple(item.value for item in acknowledged)
    if prior and tuple(item.note for item in prior) != expected_notes:
        raise InvalidUsDayAgentOperatingError("thesis_lifecycle_replay_mismatch")
    parent = request.thesis.thesis_id
    existing_events = services.paper_store.events(request.thesis.thesis_id)
    for index, transition in enumerate(acknowledged):
        kind = ThesisChangeKind.CLOSE if transition is UsDayOperatingTransition.RECONCILED else ThesisChangeKind.HOLD
        change = UsDayThesisChange.create(
            thesis_id=request.thesis.thesis_id,
            parent_event_id=parent,
            kind=kind,
            occurred_at=request.evaluated_at,
            note=transition.value,
        )
        _ = services.thesis_store.publish_change(change)
        parent = change.event_id
        if len(existing_events) <= index + 1:
            state = (
                RecommendationState.TIME_EXIT
                if transition in {UsDayOperatingTransition.FLAT, UsDayOperatingTransition.RECONCILED}
                else RecommendationState.ACTIVE
            )
            services.paper_store.set_state(
                request.thesis.thesis_id,
                state,
                request.evaluated_at,
                None,
                transition.value,
            )


__all__ = (
    "InvalidUsDayAgentOperatingError",
    "UsDayAgentOperatingRequest",
    "UsDayAgentOperatingServices",
    "operate_us_day_agent",
)
