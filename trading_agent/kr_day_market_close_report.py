from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    MarketCloseReport,
)
from trading_agent.day_learning_report_store import publish_market_close_report
from trading_agent.kis_kr_session_calendar import (
    next_kr_open_session,
)
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
)
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_market_close_metrics import (
    KrDayMarketCloseMetrics,
    build_kr_day_market_close_metrics,
    metrics_for_report,
    publish_kr_day_market_close_metrics,
)
from trading_agent.kr_day_market_close_projection import (
    KrDayMarketCloseProjectionInput,
    build_kr_day_market_close_report,
)
from trading_agent.kr_day_market_close_report_reader import (
    kr_market_close_reports,
    latest_kr_market_close_report,
    latest_prior_kr_market_close_reports,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_KST = ZoneInfo("Asia/Seoul")
_OFFICIAL_CLOSE = dt.time(15, 30)
_HEX64 = r"^[0-9a-f]{64}$"


class InvalidKrDayMarketCloseReportError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day market close report evidence is invalid"


class KrDayMarketCloseRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    session_date: dt.date
    official_close_at: dt.datetime
    finalized_at: dt.datetime
    calendar_snapshot: KrSessionCalendarSnapshot
    expected_capsule_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    shadow_events: tuple[KrDayCapsuleShadowEvent, ...] = Field(min_length=1)
    outcomes: tuple[KrDayCapsuleOutcome, ...] = Field(min_length=1, max_length=3)
    active_capsule_ids: tuple[str, ...] = Field(max_length=3)
    queued_capsule_ids: tuple[str, ...]
    risk_incident_ids: tuple[str, ...]
    data_incident_ids: tuple[str, ...]
    agent_version_id: str | None = Field(default=None, pattern=_HEX64)
    diagnostics: tuple[DayDecisionDiagnostic, ...] = ()

    @model_validator(mode="after")
    def validate_canonical_sets(self) -> Self:
        collections = (
            self.expected_capsule_ids,
            self.active_capsule_ids,
            self.queued_capsule_ids,
            self.risk_incident_ids,
            self.data_incident_ids,
        )
        if any(items != tuple(sorted(set(items))) for items in collections):
            raise InvalidKrDayMarketCloseReportError
        if bool(self.agent_version_id) is not bool(self.diagnostics):
            raise InvalidKrDayMarketCloseReportError
        return self


@dataclass(frozen=True, slots=True)
class KrDayMarketClosePublication:
    report: MarketCloseReport
    metrics: KrDayMarketCloseMetrics
    next_session_date: dt.date
    created: bool
    metrics_created: bool
    path: Path
    metrics_path: Path


def publish_kr_day_market_close_report(
    report_root: Path,
    request: KrDayMarketCloseRequest,
) -> KrDayMarketClosePublication:
    try:
        checked = KrDayMarketCloseRequest.model_validate(request.model_dump(mode="python"))
        next_session = _require_finalized_evidence(checked)
        existing = kr_market_close_reports(report_root)
        current = latest_kr_market_close_report(existing, checked.session_date)
        report = build_kr_day_market_close_report(
            KrDayMarketCloseProjectionInput(
                session_date=checked.session_date,
                official_close_at=checked.official_close_at,
                finalized_at=checked.finalized_at,
                calendar_snapshot_id=checked.calendar_snapshot.snapshot_id,
                outcomes=checked.outcomes,
                shadow_events=checked.shadow_events,
                active_capsule_ids=checked.active_capsule_ids,
                queued_capsule_ids=checked.queued_capsule_ids,
                risk_incident_ids=checked.risk_incident_ids,
                data_incident_ids=checked.data_incident_ids,
                agent_version_id=checked.agent_version_id,
                diagnostics=checked.diagnostics,
                existing=existing,
                current=current,
            )
        )
        if current is not None and _same_revision_content(current, report):
            prior = latest_prior_kr_market_close_reports(existing, checked.session_date)
            previous_metrics = (
                None
                if current.payload.previous_report_id is None
                else metrics_for_report(report_root, current.payload.previous_report_id)
            )
            expected_metrics = build_kr_day_market_close_metrics(
                current,
                checked.outcomes,
                diagnostics=checked.diagnostics,
                risk_incident_ids=checked.risk_incident_ids,
                data_incident_ids=checked.data_incident_ids,
                shadow_event_ids=tuple(sorted(event.event_id for event in checked.shadow_events)),
                next_review_date=next_session,
                previous_metrics=previous_metrics,
                prior_returns=tuple(item.payload.execution.modeled_return for item in prior),
            )
            replay_metrics = metrics_for_report(report_root, current.report_id)
            if replay_metrics is None:
                metrics_publication = publish_kr_day_market_close_metrics(report_root, expected_metrics)
                replay_metrics = metrics_publication.metrics
                metrics_created = metrics_publication.created
            elif replay_metrics != expected_metrics:
                raise InvalidKrDayMarketCloseReportError
            else:
                metrics_created = False
            return KrDayMarketClosePublication(
                current,
                replay_metrics,
                next_session,
                False,
                metrics_created,
                _path(report_root, current),
                report_root / f"kr_day_metrics_{replay_metrics.metrics_id}.json",
            )
        prior = latest_prior_kr_market_close_reports(existing, checked.session_date)
        previous_metrics = None if current is None else metrics_for_report(report_root, current.report_id)
        metrics = build_kr_day_market_close_metrics(
            report,
            checked.outcomes,
            diagnostics=checked.diagnostics,
            risk_incident_ids=checked.risk_incident_ids,
            data_incident_ids=checked.data_incident_ids,
            shadow_event_ids=tuple(sorted(event.event_id for event in checked.shadow_events)),
            next_review_date=next_session,
            previous_metrics=previous_metrics,
            prior_returns=tuple(item.payload.execution.modeled_return for item in prior),
        )
        path, created = publish_market_close_report(report_root, report)
        metrics_publication = publish_kr_day_market_close_metrics(report_root, metrics)
        return KrDayMarketClosePublication(
            report,
            metrics,
            next_session,
            created,
            metrics_publication.created,
            path,
            metrics_publication.path,
        )
    except InvalidKrDayMarketCloseReportError:
        raise
    except (AttributeError, OSError, TypeError, ValidationError, ValueError) as error:
        raise InvalidKrDayMarketCloseReportError from error


def latest_kr_day_market_close_report(
    report_root: Path,
    session_date: dt.date,
) -> MarketCloseReport:
    latest = latest_kr_market_close_report(kr_market_close_reports(report_root), session_date)
    if latest is None:
        raise InvalidKrDayMarketCloseReportError
    return latest


def _require_finalized_evidence(request: KrDayMarketCloseRequest) -> dt.date:
    local_close = request.official_close_at.astimezone(_KST)
    day = next(
        (item for item in request.calendar_snapshot.payload.days if item.session_date == request.session_date),
        None,
    )
    if (
        not aware(request.official_close_at)
        or not aware(request.finalized_at)
        or local_close.date() != request.session_date
        or local_close.time().replace(tzinfo=None) != _OFFICIAL_CLOSE
        or request.finalized_at < request.official_close_at
        or day is None
        or not (day.business_day and day.trading_day and day.open_day)
    ):
        raise InvalidKrDayMarketCloseReportError
    next_session = next_kr_open_session(request.calendar_snapshot, request.session_date)
    _require_attempt_lineage(request)
    return next_session


def _require_attempt_lineage(request: KrDayMarketCloseRequest) -> None:
    expected = set(request.expected_capsule_ids)
    events_by_capsule = {
        capsule_id: tuple(event for event in request.shadow_events if event.capsule_id == capsule_id)
        for capsule_id in expected
    }
    outcomes_by_capsule = {outcome.capsule_id: outcome for outcome in request.outcomes}
    if (
        set(event.capsule_id for event in request.shadow_events) != expected
        or set(outcomes_by_capsule) != expected
        or len(outcomes_by_capsule) != len(request.outcomes)
        or set(request.active_capsule_ids) & set(request.queued_capsule_ids)
        or not set((*request.active_capsule_ids, *request.queued_capsule_ids)) <= expected
    ):
        raise InvalidKrDayMarketCloseReportError
    for capsule_id, events in events_by_capsule.items():
        outcome = outcomes_by_capsule[capsule_id]
        if (
            not events
            or any(event.session_date != request.session_date for event in events)
            or outcome.session_date != request.session_date
            or outcome.market_id != MarketId.KR_EQUITIES.value
            or outcome.terminal_event_id != events[-1].event_id
            or not _event_is_close_finalizable(events[-1])
        ):
            raise InvalidKrDayMarketCloseReportError


def _event_is_close_finalizable(event: KrDayCapsuleShadowEvent) -> bool:
    return event.terminal or event.status is KrDayCapsuleShadowStatus.REGISTERED


def _same_revision_content(left: MarketCloseReport, right: MarketCloseReport) -> bool:
    excluded = {"revision", "previous_report_id"}
    return left.payload.model_dump(mode="python", exclude=excluded) == right.payload.model_dump(
        mode="python", exclude=excluded
    )


def _path(root: Path, report: MarketCloseReport) -> Path:
    return root / f"market_close_report_{report.report_id}.json"


__all__ = (
    "InvalidKrDayMarketCloseReportError",
    "KrDayMarketClosePublication",
    "KrDayMarketCloseRequest",
    "latest_kr_day_market_close_report",
    "publish_kr_day_market_close_report",
)
