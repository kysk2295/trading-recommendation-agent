from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.day_learning_report_models import (
    DailyLearningReport,
    InvalidDayLearningReportError,
    MarketCloseReport,
    MarketCloseReportPayload,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId


def seal_market_close_report(payload: MarketCloseReportPayload) -> MarketCloseReport:
    checked = MarketCloseReportPayload.model_validate(payload.model_dump(mode="python"))
    report_id = hashlib.sha256(canonical_experiment_ledger_json(checked).encode()).hexdigest()
    return MarketCloseReport(report_id=report_id, payload=checked)


def build_daily_learning_report(
    us_report: MarketCloseReport,
    kr_report: MarketCloseReport,
    *,
    generated_at: dt.datetime,
) -> DailyLearningReport:
    checked_us = MarketCloseReport.model_validate(us_report.model_dump(mode="python"))
    checked_kr = MarketCloseReport.model_validate(kr_report.model_dump(mode="python"))
    if (
        checked_us.payload.market_id is not MarketId.US_EQUITIES
        or checked_kr.payload.market_id is not MarketId.KR_EQUITIES
    ):
        raise InvalidDayLearningReportError("day_learning_facade_market_invalid")
    return DailyLearningReport(
        us_report_id=checked_us.report_id,
        kr_report_id=checked_kr.report_id,
        generated_at=generated_at,
    )


__all__ = ("build_daily_learning_report", "seal_market_close_report")
