from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from tests.day_historical_evidence_support import (
    completed_day_evidence_context,
    historical_evidence_request,
)
from trading_agent.day_historical_evidence import (
    DayEvidenceWindow,
    DayHistoricalPreregistration,
    DaySelectionDiagnostics,
    InvalidDayHistoricalEvidenceError,
    build_day_historical_evidence,
)
from trading_agent.generated_intraday_evaluator import (
    GeneratedIntradayEvaluationError,
    validate_generated_intraday_evaluation_scope,
)
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.strategy_research_types import TerminalOutcome


def test_generated_evaluator_rejects_full_universe_and_more_than_ten_gib(tmp_path: Path) -> None:
    forbidden = tmp_path / "data" / "regend_us_stocks" / "minute.csv"

    with pytest.raises(GeneratedIntradayEvaluationError, match="full_universe"):
        validate_generated_intraday_evaluation_scope(forbidden, 9.5)
    with pytest.raises(GeneratedIntradayEvaluationError, match="rss_limit"):
        validate_generated_intraday_evaluation_scope(tmp_path / "bounded.csv", 10.1)


def test_builder_uses_ledger_as_authority_and_redacts_holdout_feedback(tmp_path: Path) -> None:
    context = completed_day_evidence_context(tmp_path)
    request = historical_evidence_request(context)

    result = build_day_historical_evidence(request)

    assert result.seal.payload.holdout_reveal.reveal_id == context.reveal_id
    assert result.seal.payload.classification is TerminalOutcome.SUPPORTED
    assert result.feedback.classification is TerminalOutcome.SUPPORTED
    assert "exact_metrics" not in result.feedback.model_dump_json().casefold()
    mismatched = DaySelectionDiagnostics(
        market_id=request.capsule.market_id,
        input_attempt_ids=("attempt-1", "attempt-2", "attempt-3"),
        total_attempted_variants=3,
        status=IntradayOverfitDiagnosticsStatus.COLLECTING,
        diagnostics_artifact_ref=f"artifact://safe/{'c' * 64}",
        diagnostics_sha256="c" * 64,
    )
    with pytest.raises(InvalidDayHistoricalEvidenceError, match="attempt"):
        _ = build_day_historical_evidence(replace(request, selection_diagnostics=mismatched))

    changed_window = DayEvidenceWindow(
        start=request.preregistration.sealed_holdout.start,
        end=request.preregistration.sealed_holdout.end + dt.timedelta(hours=1),
    )
    changed_preregistration = DayHistoricalPreregistration.model_validate(
        request.preregistration.model_dump(mode="python")
        | {"sealed_holdout": changed_window}
    )
    with pytest.raises(InvalidDayHistoricalEvidenceError, match="preregistered"):
        _ = build_day_historical_evidence(
            replace(request, preregistration=changed_preregistration)
        )
