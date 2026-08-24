from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.day_agent_version_learning_support import diagnostics
from tests.kr_day_close_service_support import close_fixture
from trading_agent.day_learning_policy import ExplorationPolicy, ExplorationPolicyAction
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_close_service_request import build_kr_day_close_request
from trading_agent.kr_day_learning_policy import publish_kr_day_learning_policy
from trading_agent.kr_day_loop_engineer import (
    InvalidKrDayLoopEvidenceError,
    KrDayLoopAuthorityPaths,
    run_configured_kr_day_loop_engineer,
    select_kr_day_failure,
)
from trading_agent.kr_day_market_close_metrics import KrDayMarketCloseMetrics
from trading_agent.kr_day_market_close_report import publish_kr_day_market_close_report


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


@pytest.mark.parametrize(
    ("preliminary", "mismatch"),
    (
        ("incident", "report_id"),
        ("insufficient", "revision"),
        ("zero_completed", "policy"),
        ("version_missing", "calendar"),
    ),
)
def test_configured_zero_paths_reject_mismatched_canonical_lineage(
    preliminary: str,
    mismatch: str,
    tmp_path: Path,
) -> None:
    # Given: a would-be zero result whose canonical report/metrics/policy lineage is mismatched.
    fixture = close_fixture(tmp_path)
    request = build_kr_day_close_request(fixture.config, fixture.post_close)
    if preliminary == "incident":
        request = request.model_copy(update={"data_incident_ids": ("incident",)})
    if preliminary == "version_missing":
        request = request.model_copy(
            update={
                "agent_version_id": "f" * 64,
                "diagnostics": tuple(
                    item.model_copy(update={"evidence_ids": (request.shadow_events[-1].event_id,)})
                    for item in diagnostics()
                ),
            }
        )
    publication = publish_kr_day_market_close_report(fixture.config.report_root, request)
    policy = publish_kr_day_learning_policy(
        fixture.config.report_root,
        fixture.config.policy_root,
        publication.report,
        request.calendar_snapshot,
        ExplorationPolicyAction.KEEP,
    ).policy
    metrics = publication.metrics
    if preliminary == "zero_completed":
        metrics = _changed_metrics(metrics, completed_count=0)
    metrics, policy = _mismatched_lineage(metrics, policy, mismatch)

    # When / Then: configured execution validates lineage before returning any zero result.
    with pytest.raises(InvalidKrDayLoopEvidenceError):
        _ = run_configured_kr_day_loop_engineer(
            publication.report,
            metrics,
            policy,
            KrDayLoopAuthorityPaths(fixture.config.state_root, fixture.config.experiment_ledger),
        )


def _changed_metrics(
    metrics: KrDayMarketCloseMetrics,
    **updates: str | int,
) -> KrDayMarketCloseMetrics:
    payload = metrics.payload.model_copy(update=updates)
    identity = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return KrDayMarketCloseMetrics(metrics_id=identity, payload=payload)


def _mismatched_lineage(
    metrics: KrDayMarketCloseMetrics,
    policy: ExplorationPolicy,
    mismatch: str,
) -> tuple[KrDayMarketCloseMetrics, ExplorationPolicy]:
    if mismatch == "report_id":
        return _changed_metrics(metrics, report_id="e" * 64), policy
    if mismatch == "revision":
        return _changed_metrics(metrics, revision=2, previous_metrics_id="d" * 64), policy
    payload = policy.payload.model_copy(
        update=(
            {"final_report_id": "c" * 64}
            if mismatch == "policy"
            else {"calendar_snapshot_id": f"calendar://official/XKRX/{'b' * 64}"}
        )
    )
    identity = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return metrics, ExplorationPolicy(policy_id=identity, payload=payload)
