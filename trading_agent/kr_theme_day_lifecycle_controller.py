from __future__ import annotations

import datetime as dt
from typing import Final, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.kis_kr_session_calendar import (
    InvalidKisKrSessionCalendarError,
    next_kr_open_session,
)
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_theme_day_lifecycle_models import (
    KrThemeDayLifecycleOutcome,
    KrThemeDayLifecycleResult,
    decide_kr_theme_day_lifecycle,
)
from trading_agent.kr_theme_day_lifecycle_projection import (
    InvalidKrThemeDayLifecycleProjectionError,
    KrThemeDayLifecycleProjectionSource,
    exact_kr_theme_day_lifecycle_replay,
    kr_theme_day_lifecycle_registration_event,
    kr_theme_day_lifecycle_transition_event,
)
from trading_agent.kr_theme_day_review_models import (
    CURRENT_KR_THEME_DAY_REVIEWER_VERSION,
)
from trading_agent.kr_theme_day_review_store import (
    InvalidKrThemeDayReviewStoreError,
    kr_theme_day_review_event_key,
)
from trading_agent.kr_theme_day_reviewer import (
    InvalidKrThemeDayReviewError,
    KrThemeDayReviewRequest,
    KrThemeDayReviewSources,
    review_kr_theme_day_strategy,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent
from trading_agent.research_identity_models import AgentOperatingMode

CURRENT_KR_THEME_DAY_LIFECYCLE_POLICY: Final = "kr_theme_day_lifecycle_v1"
_KST: Final = ZoneInfo("Asia/Seoul")
_SESSION_CLOSE: Final = dt.time(15, 30)


class InvalidKrThemeDayLifecycleSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR theme day lifecycle Controller could not verify exact evidence"


class KrThemeDayLifecycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    as_of_session: dt.date
    decided_at: dt.datetime
    calendar_snapshot: KrSessionCalendarSnapshot

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        local = self.decided_at.astimezone(_KST) if _aware(self.decided_at) else None
        if (
            not self.strategy_version
            or self.strategy_version != self.strategy_version.strip()
            or local is None
            or local.date() != self.as_of_session
            or local.time() < _SESSION_CLOSE
        ):
            raise InvalidKrThemeDayLifecycleSourceError
        return self


def control_kr_theme_day_lifecycle(
    experiment_ledger: ExperimentLedgerStore,
    review_sources: KrThemeDayReviewSources,
    request: KrThemeDayLifecycleRequest,
) -> KrThemeDayLifecycleResult:
    try:
        request = KrThemeDayLifecycleRequest.model_validate(request.model_dump(mode="python"))
        source = _verified_source(experiment_ledger, review_sources, request)
        replay = exact_kr_theme_day_lifecycle_replay(
            source,
            as_of_session=request.as_of_session,
            calendar_id=request.calendar_snapshot.snapshot_id,
            policy_version=CURRENT_KR_THEME_DAY_LIFECYCLE_POLICY,
        )
        if replay is not None:
            return replay
        if not source.events:
            event = kr_theme_day_lifecycle_registration_event(
                source,
                request.decided_at,
                request.calendar_snapshot.snapshot_id,
                CURRENT_KR_THEME_DAY_LIFECYCLE_POLICY,
            )
            return _append_result(experiment_ledger, KrThemeDayLifecycleOutcome.REGISTERED, None, event, ())
        current = experiment_ledger.multi_market_lifecycle_state(request.strategy_version, request.as_of_session)
        if current is None:
            return KrThemeDayLifecycleResult(
                KrThemeDayLifecycleOutcome.NO_CHANGE,
                False,
                None,
                None,
                ("lifecycle_registration_not_effective",),
                (),
                None,
            )
        if current != source.events[-1]:
            raise InvalidKrThemeDayLifecycleSourceError
        decision = decide_kr_theme_day_lifecycle(current.event.to_state, source.review.action)
        if decision.target_state is None:
            return KrThemeDayLifecycleResult(
                KrThemeDayLifecycleOutcome.NO_CHANGE,
                False,
                current.event.to_state,
                None,
                decision.reason_codes,
                decision.blockers,
                None,
            )
        event = kr_theme_day_lifecycle_transition_event(
            source,
            current,
            decision,
            request.decided_at,
            request.calendar_snapshot.snapshot_id,
            CURRENT_KR_THEME_DAY_LIFECYCLE_POLICY,
        )
        return _append_result(
            experiment_ledger,
            KrThemeDayLifecycleOutcome.TRANSITIONED,
            current.event.to_state,
            event,
            decision.blockers,
        )
    except InvalidKrThemeDayLifecycleSourceError:
        raise
    except (
        AttributeError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKisKrSessionCalendarError,
        InvalidKrThemeDayLifecycleProjectionError,
        InvalidKrThemeDayReviewError,
        InvalidKrThemeDayReviewStoreError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayLifecycleSourceError from None


def _verified_source(
    ledger: ExperimentLedgerStore,
    review_sources: KrThemeDayReviewSources,
    request: KrThemeDayLifecycleRequest,
) -> KrThemeDayLifecycleProjectionSource:
    if review_sources.experiment_ledger.path != ledger.path:
        raise InvalidKrThemeDayLifecycleSourceError
    hypotheses = tuple(
        stored
        for stored in ledger.multi_market_hypotheses()
        if stored.registration.hypothesis_id == "H-KR-THEME-LEADER-VWAP-001"
    )
    versions = tuple(
        stored
        for stored in ledger.multi_market_strategy_versions()
        if stored.registration.strategy_version == request.strategy_version
    )
    if len(hypotheses) != 1 or len(versions) != 1:
        raise InvalidKrThemeDayLifecycleSourceError
    version = versions[0]
    if (
        version.registration.hypothesis_id != hypotheses[0].registration.hypothesis_id
        or version.registration.experiment_scope_key != hypotheses[0].registration.experiment_scope_key
        or version.registration.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or version.registration.operating_mode is not AgentOperatingMode.SHADOW
    ):
        raise InvalidKrThemeDayLifecycleSourceError
    review = review_sources.review_store.review_event(
        request.strategy_version,
        request.as_of_session.isoformat(),
        CURRENT_KR_THEME_DAY_REVIEWER_VERSION,
    )
    if review is None or review.reviewed_at > request.decided_at:
        raise InvalidKrThemeDayLifecycleSourceError
    replay = review_kr_theme_day_strategy(
        review_sources,
        KrThemeDayReviewRequest(
            strategy_version=request.strategy_version,
            as_of_session=request.as_of_session,
            reviewed_at=review.reviewed_at,
        ),
    )
    if replay.created or replay.event != review:
        raise InvalidKrThemeDayLifecycleSourceError
    snapshot = request.calendar_snapshot
    if snapshot.payload.base_date != request.as_of_session or snapshot.payload.observed_at > review.reviewed_at:
        raise InvalidKrThemeDayLifecycleSourceError
    return KrThemeDayLifecycleProjectionSource(
        hypotheses[0],
        version,
        review,
        kr_theme_day_review_event_key(review),
        next_kr_open_session(snapshot, request.as_of_session),
        ledger.multi_market_lifecycle_events(request.strategy_version),
    )


def _append_result(
    ledger: ExperimentLedgerStore,
    outcome: KrThemeDayLifecycleOutcome,
    previous_state: StrategyLifecycleState | None,
    event: MultiMarketStrategyLifecycleEvent,
    blockers: tuple[str, ...],
) -> KrThemeDayLifecycleResult:
    with ledger.writer() as writer:
        created = writer.append_multi_market_lifecycle_event(event)
    return KrThemeDayLifecycleResult(
        outcome,
        created,
        previous_state,
        event.to_state,
        event.reason_codes,
        blockers,
        event,
    )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
