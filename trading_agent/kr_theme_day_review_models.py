from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_models import TrialEventKind

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
CURRENT_KR_THEME_DAY_REVIEWER_VERSION: Final = "kr_theme_day_reviewer_v1"
MINIMUM_FORWARD_SESSIONS: Final = 20
MINIMUM_COMPLETED_SIGNALS: Final = 30
_AUTHORITY_BLOCKERS: Final = (
    "allocation_change_forbidden",
    "automatic_state_change_forbidden",
    "paper_authority_forbidden",
)


class KrThemeDayReviewAction(StrEnum):
    CONTINUE_COLLECTION = "continue_collection"
    DATA_QUALITY_REVIEW = "data_quality_review"
    COMPARISON_READY = "comparison_ready"


class InvalidKrThemeDayReviewModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day review model is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDayReviewCounts:
    completed_sessions: int
    censored_sessions: int
    failed_sessions: int
    completed_trades: int


@dataclass(frozen=True, slots=True)
class KrThemeDayReviewDecision:
    action: KrThemeDayReviewAction
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]


def decide_kr_theme_day_review(counts: KrThemeDayReviewCounts) -> KrThemeDayReviewDecision:
    values = (
        counts.completed_sessions,
        counts.censored_sessions,
        counts.failed_sessions,
        counts.completed_trades,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise InvalidKrThemeDayReviewModelError
    blockers: set[str] = set(_AUTHORITY_BLOCKERS)
    reasons: set[str] = set()
    if counts.censored_sessions:
        reasons.add("censored_evidence_present")
        blockers.add(f"censored_sessions:{counts.censored_sessions}")
    if counts.failed_sessions:
        reasons.add("failed_evidence_present")
        blockers.add(f"failed_sessions:{counts.failed_sessions}")
    if reasons:
        _add_minimum_blockers(blockers, counts)
        return KrThemeDayReviewDecision(
            KrThemeDayReviewAction.DATA_QUALITY_REVIEW,
            tuple(sorted(reasons)),
            tuple(sorted(blockers)),
        )
    if counts.completed_sessions < MINIMUM_FORWARD_SESSIONS or counts.completed_trades < MINIMUM_COMPLETED_SIGNALS:
        _add_minimum_blockers(blockers, counts)
        return KrThemeDayReviewDecision(
            KrThemeDayReviewAction.CONTINUE_COLLECTION,
            ("forward_evidence_collecting",),
            tuple(sorted(blockers)),
        )
    blockers.update(("independent_comparator_missing", "multiple_testing_evidence_missing"))
    return KrThemeDayReviewDecision(
        KrThemeDayReviewAction.COMPARISON_READY,
        ("minimum_forward_evidence_satisfied",),
        tuple(sorted(blockers)),
    )


class KrThemeDayReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    as_of_session: dt.date
    reviewer_version: str
    trial_ids: tuple[str, ...]
    terminal_event_keys: tuple[str, ...]
    terminal_artifact_sha256s: tuple[str, ...]
    terminal_kinds: tuple[TrialEventKind, ...]
    completed_sessions: int
    censored_sessions: int
    failed_sessions: int
    completed_trades: int
    trade_exit_ids: tuple[str, ...]
    trade_net_returns: tuple[Decimal, ...]
    trade_realized_rs: tuple[Decimal, ...]
    compounded_return: Decimal
    mean_realized_r: Decimal
    win_rate: Decimal
    max_drawdown: Decimal
    action: KrThemeDayReviewAction
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    reviewed_at: dt.datetime
    automatic_state_change_allowed: Literal[False]
    order_authority_change_allowed: Literal[False]
    allocation_change_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        session_count = len(self.trial_ids)
        if (
            not self.strategy_version
            or not self.reviewer_version
            or not _unique_texts(self.trial_ids)
            or not _aligned_hashes(session_count, self.terminal_event_keys)
            or not _aligned_hashes(session_count, self.terminal_artifact_sha256s)
            or len(self.terminal_kinds) != session_count
            or any(kind is TrialEventKind.STARTED for kind in self.terminal_kinds)
            or self.completed_sessions != self.terminal_kinds.count(TrialEventKind.COMPLETED)
            or self.censored_sessions != self.terminal_kinds.count(TrialEventKind.CENSORED)
            or self.failed_sessions != self.terminal_kinds.count(TrialEventKind.FAILED)
            or self.completed_sessions + self.censored_sessions + self.failed_sessions != session_count
            or not _aligned_trades(self)
            or not _canonical_texts(self.reasons)
            or not _canonical_texts(self.blockers)
            or not _aware(self.reviewed_at)
        ):
            raise InvalidKrThemeDayReviewModelError
        compounded, mean_r, win_rate, max_drawdown = _metrics(self.trade_net_returns, self.trade_realized_rs)
        if (
            self.compounded_return != compounded
            or self.mean_realized_r != mean_r
            or self.win_rate != win_rate
            or self.max_drawdown != max_drawdown
        ):
            raise InvalidKrThemeDayReviewModelError
        decision = decide_kr_theme_day_review(
            KrThemeDayReviewCounts(
                completed_sessions=self.completed_sessions,
                censored_sessions=self.censored_sessions,
                failed_sessions=self.failed_sessions,
                completed_trades=self.completed_trades,
            )
        )
        if self.action is not decision.action or self.reasons != decision.reasons or self.blockers != decision.blockers:
            raise InvalidKrThemeDayReviewModelError
        return self


def kr_theme_day_review_metrics(
    returns: tuple[Decimal, ...],
    realized_rs: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return _metrics(returns, realized_rs)


def _add_minimum_blockers(blockers: set[str], counts: KrThemeDayReviewCounts) -> None:
    if counts.completed_sessions < MINIMUM_FORWARD_SESSIONS:
        blockers.add(f"minimum_forward_sessions:{counts.completed_sessions}/{MINIMUM_FORWARD_SESSIONS}")
    if counts.completed_trades < MINIMUM_COMPLETED_SIGNALS:
        blockers.add(f"minimum_completed_signals:{counts.completed_trades}/{MINIMUM_COMPLETED_SIGNALS}")


def _aligned_trades(event: KrThemeDayReviewEvent) -> bool:
    count = event.completed_trades
    return (
        count >= 0
        and len(event.trade_exit_ids) == count
        and len(set(event.trade_exit_ids)) == count
        and all(_HEX64.fullmatch(value) for value in event.trade_exit_ids)
        and len(event.trade_net_returns) == count
        and len(event.trade_realized_rs) == count
        and all(_finite(value) and value > -1 for value in event.trade_net_returns)
        and all(_finite(value) for value in event.trade_realized_rs)
    )


def _metrics(
    returns: tuple[Decimal, ...],
    realized_rs: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if len(returns) != len(realized_rs) or any(not _finite(value) or value <= -1 for value in returns):
        raise InvalidKrThemeDayReviewModelError
    equity = Decimal(1)
    peak = equity
    max_drawdown = Decimal(0)
    for value in returns:
        equity *= Decimal(1) + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal(1))
    count = len(returns)
    if count == 0:
        return Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    divisor = Decimal(count)
    return (
        equity - Decimal(1),
        sum(realized_rs, start=Decimal(0)) / divisor,
        Decimal(sum(value > 0 for value in returns)) / divisor,
        max_drawdown,
    )


def _aligned_hashes(count: int, values: tuple[str, ...]) -> bool:
    return len(values) == count and len(set(values)) == count and all(_HEX64.fullmatch(value) for value in values)


def _unique_texts(values: tuple[str, ...]) -> bool:
    return (
        bool(values) and len(values) == len(set(values)) and all(value and value == value.strip() for value in values)
    )


def _canonical_texts(values: tuple[str, ...]) -> bool:
    return (
        bool(values)
        and values == tuple(sorted(set(values)))
        and all(value and value == value.strip() for value in values)
    )


def _finite(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
