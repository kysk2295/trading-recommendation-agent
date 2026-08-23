from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_learning_report_store import load_market_close_report
from trading_agent.research_identity_models import MarketId


def kr_market_close_reports(root: Path) -> tuple[MarketCloseReport, ...]:
    if not root.exists():
        return ()
    return tuple(load_market_close_report(path) for path in sorted(root.glob("market_close_report_*.json")))


def latest_kr_market_close_report(
    reports: tuple[MarketCloseReport, ...],
    session_date: dt.date,
) -> MarketCloseReport | None:
    matching = tuple(
        report
        for report in reports
        if report.payload.market_id is MarketId.KR_EQUITIES and report.payload.session_date == session_date
    )
    return max(matching, key=lambda item: item.payload.revision, default=None)


def latest_prior_kr_market_close_reports(
    reports: tuple[MarketCloseReport, ...],
    session_date: dt.date,
) -> tuple[MarketCloseReport, ...]:
    dates = sorted(
        {
            report.payload.session_date
            for report in reports
            if report.payload.market_id is MarketId.KR_EQUITIES and report.payload.session_date < session_date
        }
    )
    return tuple(
        max(
            (report for report in reports if report.payload.session_date == date),
            key=lambda item: item.payload.revision,
        )
        for date in dates
    )


__all__ = (
    "kr_market_close_reports",
    "latest_kr_market_close_report",
    "latest_prior_kr_market_close_reports",
)
