from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_learning_report_models import DayDecisionDiagnostic, MarketCloseReport
from trading_agent.day_learning_report_store import load_market_close_report
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleTerminalKind,
)
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text

_HEX64 = r"^[0-9a-f]{64}$"


class InvalidKrDayMarketCloseMetricsError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day market close metrics are invalid"


class KrDayMarketCloseMetricsPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    report_id: str = Field(pattern=_HEX64)
    previous_metrics_id: str | None = Field(default=None, pattern=_HEX64)
    session_date: dt.date
    revision: int = Field(ge=1)
    daily_cost_adjusted_shadow_return: float
    cumulative_cost_adjusted_shadow_return: float
    win_rate: float = Field(ge=0.0, le=1.0)
    mean_r: float | None
    profit_factor: float | None = Field(default=None, ge=0.0)
    daily_max_drawdown: float = Field(ge=0.0)
    cumulative_max_drawdown: float = Field(ge=0.0)
    completed_count: int = Field(ge=0)
    no_signal_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    censored_count: int = Field(ge=0)
    selection_diagnostics: tuple[DayDecisionDiagnostic, ...]
    risk_incident_ids: tuple[str, ...]
    data_incident_ids: tuple[str, ...]
    outcome_ids: tuple[str, ...] = Field(min_length=1)
    shadow_event_ids: tuple[str, ...] = ()
    next_review_date: dt.date
    provider_read_only: Literal[True] = True
    actual_return: None = None
    profitability_claim: Literal[False] = False
    cost_adjusted: Literal[True] = True

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        finite = (
            self.daily_cost_adjusted_shadow_return,
            self.cumulative_cost_adjusted_shadow_return,
            self.win_rate,
            self.daily_max_drawdown,
            self.cumulative_max_drawdown,
        )
        optional = (self.mean_r, self.profit_factor)
        canonical = (
            self.risk_incident_ids,
            self.data_incident_ids,
            self.outcome_ids,
            self.shadow_event_ids,
        )
        if (
            not all(math.isfinite(value) for value in finite)
            or any(value is not None and not math.isfinite(value) for value in optional)
            or any(items != tuple(sorted(set(items))) for items in canonical)
            or (self.revision == 1) is not (self.previous_metrics_id is None)
            or self.next_review_date <= self.session_date
        ):
            raise InvalidKrDayMarketCloseMetricsError
        return self


class KrDayMarketCloseMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    metrics_id: str = Field(pattern=_HEX64)
    payload: KrDayMarketCloseMetricsPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.metrics_id != expected:
            raise InvalidKrDayMarketCloseMetricsError
        return self


@dataclass(frozen=True, slots=True)
class KrDayMarketCloseMetricsPublication:
    metrics: KrDayMarketCloseMetrics
    path: Path
    created: bool


def build_kr_day_market_close_metrics(
    report: MarketCloseReport,
    outcomes: tuple[KrDayCapsuleOutcome, ...],
    *,
    diagnostics: tuple[DayDecisionDiagnostic, ...],
    risk_incident_ids: tuple[str, ...],
    data_incident_ids: tuple[str, ...],
    shadow_event_ids: tuple[str, ...],
    next_review_date: dt.date,
    previous_metrics: KrDayMarketCloseMetrics | None,
    prior_returns: tuple[float, ...],
) -> KrDayMarketCloseMetrics:
    returns = tuple(float(item.net_return) for item in outcomes if item.net_return is not None)
    realized_r = tuple(float(item.realized_r) for item in outcomes if item.realized_r is not None)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    payload = KrDayMarketCloseMetricsPayload(
        report_id=report.report_id,
        previous_metrics_id=None if previous_metrics is None else previous_metrics.metrics_id,
        session_date=report.payload.session_date,
        revision=report.payload.revision,
        daily_cost_adjusted_shadow_return=report.payload.execution.modeled_return,
        cumulative_cost_adjusted_shadow_return=report.payload.lineage.cumulative_modeled_return,
        win_rate=0.0 if not returns else sum(value > 0 for value in returns) / len(returns),
        mean_r=None if not realized_r else sum(realized_r) / len(realized_r),
        profit_factor=None if losses == 0 else gains / losses,
        daily_max_drawdown=_max_drawdown(returns),
        cumulative_max_drawdown=_max_drawdown((*prior_returns, report.payload.execution.modeled_return)),
        completed_count=len(returns),
        no_signal_count=sum(item.kind is KrDayCapsuleTerminalKind.NO_SIGNAL for item in outcomes),
        blocked_count=sum(item.kind is KrDayCapsuleTerminalKind.BLOCKED for item in outcomes),
        failed_count=sum(item.kind is KrDayCapsuleTerminalKind.FAILED for item in outcomes),
        censored_count=sum(item.kind is KrDayCapsuleTerminalKind.CENSORED for item in outcomes),
        selection_diagnostics=diagnostics,
        risk_incident_ids=risk_incident_ids,
        data_incident_ids=data_incident_ids,
        outcome_ids=tuple(sorted(item.outcome_id for item in outcomes)),
        shadow_event_ids=shadow_event_ids,
        next_review_date=next_review_date,
    )
    metrics_id = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return KrDayMarketCloseMetrics(metrics_id=metrics_id, payload=payload)


def publish_kr_day_market_close_metrics(
    root: Path,
    metrics: KrDayMarketCloseMetrics,
) -> KrDayMarketCloseMetricsPublication:
    path = root / f"kr_day_metrics_{metrics.metrics_id}.json"
    payload = canonical_experiment_ledger_json(metrics) + "\n"
    try:
        _require_report_binding(root, metrics)
        if path.exists():
            if read_private_text(path) != payload:
                raise InvalidKrDayMarketCloseMetricsError
            return KrDayMarketCloseMetricsPublication(metrics, path, False)
        created = publish_private_immutable_text(path, payload)
        return KrDayMarketCloseMetricsPublication(metrics, path, created)
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayMarketCloseMetricsError from None


def metrics_for_report(root: Path, report_id: str) -> KrDayMarketCloseMetrics | None:
    try:
        matches = tuple(
            metrics
            for path in sorted(root.glob("kr_day_metrics_*.json"))
            for metrics in (_load_metrics(path),)
            if metrics.payload.report_id == report_id
        )
        if len(matches) > 1:
            raise InvalidKrDayMarketCloseMetricsError
        return matches[0] if matches else None
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayMarketCloseMetricsError from None


def _require_report_binding(root: Path, metrics: KrDayMarketCloseMetrics) -> None:
    report = load_market_close_report(root / f"market_close_report_{metrics.payload.report_id}.json")
    previous = (
        None
        if report.payload.previous_report_id is None
        else metrics_for_report(root, report.payload.previous_report_id)
    )
    if (
        metrics.payload.session_date != report.payload.session_date
        or metrics.payload.revision != report.payload.revision
        or metrics.payload.previous_metrics_id != (None if previous is None else previous.metrics_id)
    ):
        raise InvalidKrDayMarketCloseMetricsError


def _load_metrics(path: Path) -> KrDayMarketCloseMetrics:
    raw = read_private_text(path)
    metrics = KrDayMarketCloseMetrics.model_validate_json(raw)
    if path.name != f"kr_day_metrics_{metrics.metrics_id}.json" or raw != (
        canonical_experiment_ledger_json(metrics) + "\n"
    ):
        raise InvalidKrDayMarketCloseMetricsError
    return metrics


def _max_drawdown(returns: tuple[float, ...]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


__all__ = (
    "InvalidKrDayMarketCloseMetricsError",
    "KrDayMarketCloseMetrics",
    "KrDayMarketCloseMetricsPayload",
    "KrDayMarketCloseMetricsPublication",
    "build_kr_day_market_close_metrics",
    "metrics_for_report",
    "publish_kr_day_market_close_metrics",
)
