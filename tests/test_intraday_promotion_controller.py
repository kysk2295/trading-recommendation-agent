from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

import trading_agent.intraday_promotion_controller as promotion
from tests.test_lifecycle_controller import ORB_VERSION, _seed_base_sources
from trading_agent.experiment_ledger_keys import (
    experiment_trial_event_key,
    research_source_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    ResearchSourceKind,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriter,
)
from trading_agent.intraday_promotion_controller import (
    INTRADAY_PROMOTION_POLICY_VERSION,
    IntradayPromotionApprovalRequest,
    IntradayPromotionControlCommand,
    IntradayPromotionRequest,
    approve_intraday_promotion,
    assess_intraday_promotion,
    control_intraday_promotion,
)
from trading_agent.intraday_promotion_evidence import (
    IntradayPromotionEvidencePaths,
    VerifiedIntradayPromotionEvidence,
)
from trading_agent.intraday_promotion_models import PromotionAssessmentStatus

SESSION = dt.date(2026, 7, 16)
ASSESS_AT = dt.datetime(2026, 7, 16, 20, 10, tzinfo=dt.UTC)
APPROVE_AT = dt.datetime(2026, 7, 16, 20, 20, tzinfo=dt.UTC)
CONTROL_AT = dt.datetime(2026, 7, 16, 20, 30, tzinfo=dt.UTC)
PROMOTION_VERSION = "orb_promotion_test_v1"


def test_full_evidence_remains_blocked_without_manual_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a persisted challenger and complete automatic evidence
    request = _request(tmp_path, monkeypatch)

    # When: the automatic assessment is persisted
    assessment, _, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")

    # Then: it grants no authority and names the durable manual gate
    assert assessment.content.blockers == ("manual_approval_required",)
    assert request.experiment_ledger.exists()
    assert _promotion_binding_count(request) == 0


def test_assessment_requires_challenger_state_effective_for_the_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, monkeypatch)
    prior_session = dt.date(2026, 7, 15)
    prior_request = replace(request, session_date=prior_session)
    evidence = VerifiedIntradayPromotionEvidence(
        strategy_version=PROMOTION_VERSION,
        evidence_keys=tuple(chr(code) * 64 for code in range(ord("a"), ord("g"))),
        observed_at=(dt.datetime(2026, 7, 15, 19, tzinfo=dt.UTC),) * 6,
        blockers=(),
    )
    monkeypatch.setattr(
        promotion,
        "load_intraday_promotion_evidence",
        lambda _paths, _date: evidence,
    )
    with pytest.raises(
        promotion.InvalidIntradayPromotionError,
        match="challenger_state_required",
    ):
        _ = assess_intraday_promotion(
            prior_request,
            dt.datetime(2026, 7, 15, 20, 10, tzinfo=dt.UTC),
            tmp_path / "assessments-prior-state",
        )


def test_approved_transition_is_next_session_and_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: complete evidence plus a distinct persisted manual approval
    request = _request(tmp_path, monkeypatch)
    _, assessment_path, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")
    _, approval_path, _ = approve_intraday_promotion(
        IntradayPromotionApprovalRequest(assessment_path, "operator_1", APPROVE_AT, tmp_path / "approvals")
    )
    command = IntradayPromotionControlCommand(request, assessment_path, approval_path, CONTROL_AT)

    # When: control is invoked and replayed
    first = control_intraday_promotion(command)
    replay = control_intraday_promotion(command)

    # Then: one shadow champion event and one authority binding persist
    assert (first.authority_bindings_created, first.lifecycle_events_created) == (1, 1)
    assert (replay.authority_bindings_created, replay.lifecycle_events_created) == (0, 0)
    assert first.event == replay.event
    assert first.event.policy_version == INTRADAY_PROMOTION_POLICY_VERSION
    assert first.event.to_state is StrategyLifecycleState.SHADOW_CHAMPION
    assert first.event.effective_session_date == dt.date(2026, 7, 17)


def test_event_conflict_rolls_back_authority_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved transition whose lifecycle append conflicts
    request = _request(tmp_path, monkeypatch)
    _, assessment_path, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")
    _, approval_path, _ = approve_intraday_promotion(
        IntradayPromotionApprovalRequest(assessment_path, "operator_1", APPROVE_AT, tmp_path / "approvals")
    )

    def conflict(_writer: ExperimentLedgerWriter, _event: StrategyLifecycleEvent) -> bool:
        raise ExperimentLedgerConflictError

    monkeypatch.setattr(ExperimentLedgerWriter, "append_lifecycle_event", conflict)

    # When: the atomic writer transaction reaches the event conflict
    with pytest.raises(ExperimentLedgerConflictError):
        _ = control_intraday_promotion(
            IntradayPromotionControlCommand(request, assessment_path, approval_path, CONTROL_AT)
        )

    # Then: the authority insert was rolled back with the event
    assert _promotion_binding_count(request) == 0


def test_collecting_or_stale_evidence_cannot_receive_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a challenger whose evidence is under-sample and stale
    request = _request(tmp_path, monkeypatch)
    blocked = VerifiedIntradayPromotionEvidence(
        strategy_version=PROMOTION_VERSION,
        evidence_keys=tuple(chr(code) * 64 for code in range(ord("a"), ord("g"))),
        observed_at=(dt.datetime(2026, 7, 15, 19, tzinfo=dt.UTC),) * 6,
        blockers=("dsr_pbo_not_ready", "stale_evidence"),
    )
    monkeypatch.setattr(promotion, "load_intraday_promotion_evidence", lambda _paths, _date: blocked)
    assessment, path, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")

    # When / Then: the explicit approval boundary refuses the blocked assessment
    assert assessment.content.blockers == (
        "dsr_pbo_not_ready",
        "manual_approval_required",
        "stale_evidence",
    )
    with pytest.raises(promotion.InvalidIntradayPromotionError):
        _ = approve_intraday_promotion(
            IntradayPromotionApprovalRequest(path, "operator_1", APPROVE_AT, tmp_path / "approvals")
        )
    assert _promotion_binding_count(request) == 0


def test_control_rejects_approval_for_a_different_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two valid assessments with different immutable evidence identities
    request = _request(tmp_path, monkeypatch)
    _, first_path, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")
    alternate = VerifiedIntradayPromotionEvidence(
        strategy_version=PROMOTION_VERSION,
        evidence_keys=tuple(str(value) * 64 for value in range(6)),
        observed_at=(dt.datetime(2026, 7, 16, 19, tzinfo=dt.UTC),) * 6,
        blockers=(),
    )
    monkeypatch.setattr(promotion, "load_intraday_promotion_evidence", lambda _paths, _date: alternate)
    _, second_path, _ = assess_intraday_promotion(request, ASSESS_AT, tmp_path / "assessments")
    _, approval_path, _ = approve_intraday_promotion(
        IntradayPromotionApprovalRequest(second_path, "operator_1", APPROVE_AT, tmp_path / "approvals")
    )

    # When / Then: cross-wired approval is rejected before authority mutation
    with pytest.raises(promotion.InvalidIntradayPromotionError):
        _ = control_intraday_promotion(IntradayPromotionControlCommand(request, first_path, approval_path, CONTROL_AT))
    assert _promotion_binding_count(request) == 0


def test_source_bound_assessment_rejects_mismatched_trial_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a source-backed challenger and complete evidence naming a different dataset.
    request = _request(tmp_path, monkeypatch)
    evidence = _seed_source_bound_trial(request)
    mismatched = replace(evidence, data_version="f" * 64)
    monkeypatch.setattr(
        promotion,
        "load_intraday_promotion_evidence",
        lambda _paths, _date: mismatched,
    )

    # When / Then: assessment stops before a manual-approval artifact is published.
    with pytest.raises(
        promotion.InvalidIntradayPromotionError,
        match="strategy_lineage_invalid",
    ):
        _ = assess_intraday_promotion(
            request,
            ASSESS_AT,
            tmp_path / "assessments-mismatched",
        )
    assert not (tmp_path / "assessments-mismatched").exists()
    assert _promotion_binding_count(request) == 0


def test_source_bound_assessment_waits_for_manual_approval_and_replays_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a source-backed challenger with exact code, dataset, trial, and review identities.
    request = _request(tmp_path, monkeypatch)
    evidence = _seed_source_bound_trial(request)
    monkeypatch.setattr(
        promotion,
        "load_intraday_promotion_evidence",
        lambda _paths, _date: evidence,
    )

    # When: the complete automatic assessment is run and replayed without operator approval.
    first, first_path, first_created = assess_intraday_promotion(
        request,
        ASSESS_AT,
        tmp_path / "assessments-source-bound",
    )
    replay, replay_path, replay_created = assess_intraday_promotion(
        request,
        ASSESS_AT,
        tmp_path / "assessments-source-bound",
    )

    # Then: one immutable manual wait exists and no champion or authority row is created.
    assert first.content.blockers == ("manual_approval_required",)
    assert first.content.status is PromotionAssessmentStatus.MANUAL_APPROVAL_PENDING
    assert replay == first
    assert replay_path == first_path
    assert (first_created, replay_created) == (True, False)
    assert _promotion_binding_count(request) == 0
    events = ExperimentLedgerStore(request.experiment_ledger).lifecycle_events(
        PROMOTION_VERSION
    )
    assert events[-1].event.to_state is StrategyLifecycleState.CHALLENGER


def _request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IntradayPromotionRequest:
    _, _, ledger = _seed_base_sources(tmp_path)
    original = next(
        stored.registration
        for stored in ledger.strategy_versions()
        if stored.registration.strategy_version == ORB_VERSION
    )
    version = original.model_copy(update={"strategy_version": PROMOTION_VERSION})
    hypothesis_key = next(
        str(stored.registration_key)
        for stored in ledger.hypotheses()
        if stored.registration.hypothesis_id == version.hypothesis_id
    )
    registration = StrategyLifecycleEvent(
        strategy_version=PROMOTION_VERSION,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="test_import_v1",
        decision_session_date=dt.date(2026, 7, 14),
        effective_session_date=dt.date(2026, 7, 15),
        decided_at=dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC),
        evidence_keys=tuple(
            sorted(
                (
                    hypothesis_key,
                    version.experiment_scope_key,
                    str(strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("existing_contract_import",),
        previous_event_key=None,
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(version)
        assert writer.append_lifecycle_event(registration)
    events = ledger.lifecycle_events(PROMOTION_VERSION)
    previous = events[-1]
    challenger = StrategyLifecycleEvent(
        strategy_version=PROMOTION_VERSION,
        sequence=previous.event.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=previous.event.to_state,
        to_state=StrategyLifecycleState.CHALLENGER,
        policy_version="test_challenger_v1",
        decision_session_date=dt.date(2026, 7, 15),
        effective_session_date=SESSION,
        decided_at=dt.datetime(2026, 7, 15, 20, 30, tzinfo=dt.UTC),
        evidence_keys=tuple(sorted((str(previous.event_key), "d" * 64))),
        reason_codes=("comparison_ready",),
        previous_event_key=previous.event_key,
    )
    with ledger.writer() as writer:
        assert writer.append_lifecycle_event(challenger)
    evidence = VerifiedIntradayPromotionEvidence(
        strategy_version=PROMOTION_VERSION,
        evidence_keys=tuple(chr(code) * 64 for code in range(ord("a"), ord("g"))),
        observed_at=(dt.datetime(2026, 7, 16, 19, tzinfo=dt.UTC),) * 6,
        blockers=(),
    )
    monkeypatch.setattr(promotion, "load_intraday_promotion_evidence", lambda _paths, _date: evidence)
    placeholder = tmp_path / "unused"
    return IntradayPromotionRequest(
        experiment_ledger=ledger.path,
        evidence=IntradayPromotionEvidencePaths(
            placeholder,
            placeholder,
            placeholder,
            placeholder,
            placeholder,
            placeholder,
        ),
        session_date=SESSION,
    )


def _promotion_binding_count(request: IntradayPromotionRequest) -> int:
    return sum(
        1
        for stored in promotion.ExperimentLedgerStore(request.experiment_ledger).strategy_authority_bindings()
        if stored.binding.strategy_version == PROMOTION_VERSION
    )


def _seed_source_bound_trial(
    request: IntradayPromotionRequest,
) -> VerifiedIntradayPromotionEvidence:
    ledger = ExperimentLedgerStore(request.experiment_ledger)
    version = next(
        stored.registration
        for stored in ledger.strategy_versions()
        if stored.registration.strategy_version == PROMOTION_VERSION
    )
    hypothesis = next(
        stored.registration
        for stored in ledger.hypotheses()
        if stored.registration.hypothesis_id == version.hypothesis_id
    )
    source = ResearchSource(
        source_id="promotion-source-v1",
        source_kind=ResearchSourceKind.ACADEMIC_PAPER,
        title="Promotion source lineage",
        source_url="https://example.com/promotion-source-v1",
        published_on=dt.date(2025, 1, 1),
        claim="A bounded source-backed challenger requires exact empirical lineage.",
        limitations="Fixture evidence does not establish profitability.",
        retrieved_at=dt.datetime(2025, 1, 2, 19, tzinfo=dt.UTC),
        ledger_recorded_at=dt.datetime(2025, 1, 2, 19, tzinfo=dt.UTC),
    )
    card = ResearchHypothesisCard(
        hypothesis=hypothesis,
        research_source_keys=(str(research_source_key(source)),),
        economic_mechanism="The registered mechanism is evaluated out of sample.",
        counterfactual_baseline="The matched baseline omits the registered mechanism.",
    )
    trial = ExperimentTrialRegistration(
        trial_id="promotion-source-trial-v1",
        strategy_version=PROMOTION_VERSION,
        trial_kind=TrialKind.HISTORICAL_REPLAY,
        experiment_scope=hypothesis.experiment_scope,
        experiment_scope_key=hypothesis.experiment_scope_key,
        evaluator_version="intraday_walk_forward_v2",
        data_version="1" * 64,
        feed_entitlement="bounded source-backed completed bars; no broker access",
        planned_start=dt.date(2026, 7, 17),
        planned_end=dt.date(2026, 7, 17),
        registered_at=dt.datetime(2026, 7, 16, 12, tzinfo=dt.UTC),
        evidence_budget=("max_sessions:60",),
    )
    started = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 16, 12, 1, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    completed = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=dt.datetime(2026, 7, 16, 12, 2, tzinfo=dt.UTC),
        artifact_sha256s=("c" * 64,),
        reason_codes=(),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        assert writer.register_research_source(source)
        assert writer.register_research_hypothesis(card)
        assert writer.register_trial(trial)
        assert writer.append_trial_event(started)
        assert writer.append_trial_event(completed)
    return VerifiedIntradayPromotionEvidence(
        strategy_version=PROMOTION_VERSION,
        evidence_keys=tuple(chr(code) * 64 for code in range(ord("a"), ord("g"))),
        observed_at=(dt.datetime(2026, 7, 16, 19, tzinfo=dt.UTC),) * 6,
        blockers=(),
        trial_id=trial.trial_id,
        experiment_artifact_id="c" * 64,
        review_artifact_id="d" * 64,
        data_version=trial.data_version,
        evaluator_version=trial.evaluator_version,
        strategy_code_version=version.code_version,
    )
