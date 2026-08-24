from __future__ import annotations

import pytest

from tests.day_agent_version_learning_support import diagnostics
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
)
from trading_agent.kr_day_loop_engineer import select_kr_day_failure


@pytest.mark.parametrize("selected_stage", tuple(DayDecisionStage))
def test_stage_mapping_selects_each_exact_refuted_stage(selected_stage: DayDecisionStage) -> None:
    # Given: the complete ordered taxonomy with one exact refuted stage.
    evidence = tuple(
        DayDecisionDiagnostic(
            stage=stage,
            outcome=DayDecisionOutcome.REFUTED if stage is selected_stage else DayDecisionOutcome.SUPPORTED,
            score=0.1 if stage is selected_stage else 0.9,
            evidence_ids=(f"evidence:{stage.value}",),
            reason_codes=("refuted",) if stage is selected_stage else ("supported",),
        )
        for stage in DayDecisionStage
    )

    # When: the deterministic KR adapter selects a research problem.
    selected = select_kr_day_failure(evidence)

    # Then: the existing DayDecisionStage member is returned without translation aliases.
    assert selected is not None
    assert selected.stage is selected_stage


def test_lowest_confidence_tie_uses_taxonomy_order() -> None:
    # Given: two equally low-confidence failures in reverse input order.
    source = diagnostics()
    evidence = tuple(
        item.model_copy(
            update={
                "outcome": (
                    DayDecisionOutcome.REFUTED
                    if item.stage in {DayDecisionStage.THEME_SELECTION, DayDecisionStage.ENTRY}
                    else DayDecisionOutcome.SUPPORTED
                ),
                "score": (
                    0.2
                    if item.stage in {DayDecisionStage.THEME_SELECTION, DayDecisionStage.ENTRY}
                    else 0.8
                ),
            }
        )
        for item in reversed(source)
    )

    # When: selection evaluates confidence and the canonical taxonomy rank.
    selected = select_kr_day_failure(evidence)

    # Then: theme_selection wins the exact tie.
    assert selected is not None
    assert selected.stage is DayDecisionStage.THEME_SELECTION


def test_supported_or_inconclusive_evidence_creates_no_failure() -> None:
    # Given: complete evidence without a refuted stage.
    evidence = tuple(
        item.model_copy(update={"outcome": DayDecisionOutcome.INCONCLUSIVE})
        for item in diagnostics()
    )

    # When / Then: insufficient failure evidence truthfully selects nothing.
    assert select_kr_day_failure(evidence) is None
