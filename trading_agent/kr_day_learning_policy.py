from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.day_learning_policy import (
    ExplorationPolicy,
    ExplorationPolicyAction,
    ExplorationPolicyRequest,
    OfficialNextSessionCalendarSnapshot,
    build_exploration_policy,
)
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_kr_session_calendar import next_kr_open_session
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_day_market_close_report import latest_kr_day_market_close_report
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text
from trading_agent.research_identity_models import MarketId


class InvalidKrDayLearningPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day learning policy input is invalid"


@dataclass(frozen=True, slots=True)
class KrDayLearningPolicyPublication:
    policy: ExplorationPolicy
    created: bool
    path: Path


def publish_kr_day_learning_policy(
    report_root: Path,
    policy_root: Path,
    report: MarketCloseReport,
    calendar: KrSessionCalendarSnapshot,
    action: ExplorationPolicyAction,
) -> KrDayLearningPolicyPublication:
    try:
        checked_report = MarketCloseReport.model_validate(report.model_dump(mode="python"))
        checked_calendar = KrSessionCalendarSnapshot.model_validate(calendar.model_dump(mode="python"))
        latest = latest_kr_day_market_close_report(report_root, checked_report.payload.session_date)
        if (
            checked_report.payload.market_id is not MarketId.KR_EQUITIES
            or checked_report != latest
            or checked_report.payload.finalized_at < checked_report.payload.watermark.finalized_through
        ):
            raise InvalidKrDayLearningPolicyError
        next_session = next_kr_open_session(checked_calendar, checked_report.payload.session_date)
        official = OfficialNextSessionCalendarSnapshot(
            calendar_snapshot_id=f"calendar://official/XKRX/{checked_calendar.snapshot_id}",
            market_id=MarketId.KR_EQUITIES,
            report_session_date=checked_report.payload.session_date,
            effective_session_date=next_session,
            observed_at=checked_calendar.payload.observed_at,
        )
        policy = build_exploration_policy(
            ExplorationPolicyRequest(
                latest_final_report=checked_report,
                feedback=(),
                calendar=official,
                action=action,
                effective_at=max(checked_report.payload.finalized_at, checked_calendar.payload.observed_at),
            )
        )
        path = policy_root / f"kr_day_policy_{policy.policy_id}.json"
        payload = canonical_experiment_ledger_json(policy) + "\n"
        if path.exists():
            if read_private_text(path) != payload:
                raise InvalidKrDayLearningPolicyError
            return KrDayLearningPolicyPublication(policy, False, path)
        created = publish_private_immutable_text(path, payload)
        return KrDayLearningPolicyPublication(policy, created, path)
    except InvalidKrDayLearningPolicyError:
        raise
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayLearningPolicyError from None


__all__ = (
    "InvalidKrDayLearningPolicyError",
    "KrDayLearningPolicyPublication",
    "publish_kr_day_learning_policy",
)
