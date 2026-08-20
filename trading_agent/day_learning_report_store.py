from __future__ import annotations

import math
from pathlib import Path

from pydantic import ValidationError

from trading_agent.day_learning_report_models import (
    InvalidDayLearningReportError,
    MarketCloseReport,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


def publish_market_close_report(
    root: Path,
    report: MarketCloseReport,
) -> tuple[Path, bool]:
    try:
        checked = MarketCloseReport.model_validate(report.model_dump(mode="python"))
        path = root / f"market_close_report_{checked.report_id}.json"
        if path.exists():
            if load_market_close_report(path) != checked:
                raise InvalidDayLearningReportError("day_report_identity_conflict")
            return path, False
        existing = tuple(load_market_close_report(candidate) for candidate in root.glob("market_close_report_*.json"))
        _require_cumulative_chain(existing, checked)
        _require_revision_chain(existing, checked)
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, ValidationError):
        raise InvalidDayLearningReportError("day_report_store_invalid") from None


def load_market_close_report(path: Path) -> MarketCloseReport:
    try:
        raw = read_private_text(path)
        report = MarketCloseReport.model_validate_json(raw)
        if path.name != f"market_close_report_{report.report_id}.json" or raw != _payload(report):
            raise InvalidDayLearningReportError("day_report_store_tampered")
        return report
    except InvalidDayLearningReportError:
        raise
    except (InvalidPrivateImmutableFileError, ValidationError):
        raise InvalidDayLearningReportError("day_report_store_invalid") from None


def _require_revision_chain(
    existing: tuple[MarketCloseReport, ...],
    report: MarketCloseReport,
) -> None:
    payload = report.payload
    lineage = tuple(
        item
        for item in existing
        if item.payload.market_id is payload.market_id
        and item.payload.session_date == payload.session_date
        and item.payload.watermark.watermark_id == payload.watermark.watermark_id
    )
    if payload.revision == 1:
        if lineage:
            raise InvalidDayLearningReportError("day_report_initial_final_conflict")
        return
    previous = next((item for item in lineage if item.report_id == payload.previous_report_id), None)
    latest = max(lineage, key=lambda item: item.payload.revision, default=None)
    if (
        previous is None
        or latest is not previous
        or any(item.payload.revision == payload.revision for item in lineage)
        or previous.payload.revision + 1 != payload.revision
    ):
        raise InvalidDayLearningReportError("day_report_revision_chain_invalid")


def _require_cumulative_chain(
    existing: tuple[MarketCloseReport, ...],
    report: MarketCloseReport,
) -> None:
    payload = report.payload
    market_reports = tuple(
        item for item in existing if item.payload.market_id is payload.market_id
    )
    if any(item.payload.session_date > payload.session_date for item in market_reports):
        raise InvalidDayLearningReportError("day_report_cumulative_lineage_invalid")
    prior = _cumulative_prior(market_reports, report)
    expected_modeled = _compound_return(
        0.0 if prior is None else prior.payload.lineage.cumulative_modeled_return,
        payload.execution.modeled_return,
    )
    prior_actual = None if prior is None else prior.payload.lineage.cumulative_actual_return
    current_actual = payload.execution.actual_return
    expected_actual = (
        None
        if current_actual is None or (prior is not None and prior_actual is None)
        else _compound_return(0.0 if prior_actual is None else prior_actual, current_actual)
    )
    actual = payload.lineage.cumulative_actual_return
    if not math.isclose(
        payload.lineage.cumulative_modeled_return,
        expected_modeled,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ) or not _optional_return_matches(actual, expected_actual):
        raise InvalidDayLearningReportError("day_report_cumulative_return_invalid")


def _cumulative_prior(
    market_reports: tuple[MarketCloseReport, ...],
    report: MarketCloseReport,
) -> MarketCloseReport | None:
    lineage = report.payload.lineage.lineage_report_ids
    earlier = tuple(
        item
        for item in market_reports
        if item.payload.session_date < report.payload.session_date
    )
    if not lineage:
        if earlier:
            raise InvalidDayLearningReportError("day_report_cumulative_lineage_invalid")
        return None
    prior = next((item for item in earlier if item.report_id == lineage[-1]), None)
    if prior is None:
        raise InvalidDayLearningReportError("day_report_cumulative_lineage_invalid")
    same_prior_session = tuple(
        item for item in earlier if item.payload.session_date == prior.payload.session_date
    )
    latest_prior = max(
        same_prior_session,
        key=lambda item: (
            item.payload.finalized_at,
            item.payload.revision,
            item.report_id,
        ),
    )
    if (
        latest_prior is not prior
        or lineage != (*prior.payload.lineage.lineage_report_ids, prior.report_id)
    ):
        raise InvalidDayLearningReportError("day_report_cumulative_lineage_invalid")
    return prior


def _compound_return(previous: float, current: float) -> float:
    return (1.0 + previous) * (1.0 + current) - 1.0


def _optional_return_matches(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)


def _payload(report: MarketCloseReport) -> str:
    return canonical_experiment_ledger_json(report) + "\n"


__all__ = ("load_market_close_report", "publish_market_close_report")
