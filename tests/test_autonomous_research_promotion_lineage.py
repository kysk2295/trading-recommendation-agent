from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pytest import CaptureFixture

import run_intraday_promotion as promotion_cli
from tests.test_intraday_overfit_diagnostics import _mature_candidates
from tests.test_intraday_parameter_plateau import _trace
from trading_agent.alpaca_sip_entitlement_artifacts import (
    AlpacaSipEntitlementAdmissionArtifact,
    AlpacaSipEntitlementAdmissionReason,
    AlpacaSipEntitlementAdmissionStatus,
    build_alpaca_sip_entitlement_artifact,
)
from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamConfig
from trading_agent.autonomous_research_cycle import (
    AutonomousResearchCycleConfig,
    run_autonomous_research_cycle,
)
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.daily_research_contract import strategy_contract
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
    strategy_lifecycle_event_key,
)
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerStore,
)
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import (
    GeneratedStrategyLimits,
    GeneratedStrategySandbox,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditArtifact,
    IntradayActualResearchAuditPayload,
)
from trading_agent.intraday_broker_shadow_models import (
    BROKER_SHADOW_EVIDENCE_VERSION,
    BrokerShadowEvidence,
    BrokerShadowEvidenceArtifact,
    BrokerShadowTradePair,
)
from trading_agent.intraday_broker_shadow_statistics import assess_broker_shadow_pairs
from trading_agent.intraday_equal_risk_comparison_models import (
    INTRADAY_EQUAL_RISK_COMPARISON_VERSION,
    EqualRiskComparisonArtifact,
    EqualRiskComparisonCandidate,
    EqualRiskComparisonPayload,
    EqualRiskComparisonStatus,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    INTRADAY_OVERFIT_DIAGNOSTICS_VERSION,
    IntradayOverfitCandidateTrace,
    IntradayOverfitDiagnosticsArtifact,
    IntradayOverfitDiagnosticsPayload,
    calculate_intraday_overfit_statistics,
)
from trading_agent.intraday_parameter_plateau_artifacts import (
    INTRADAY_PARAMETER_PLATEAU_VERSION,
    IntradayParameterPlateauArtifact,
    IntradayParameterPlateauPayload,
)
from trading_agent.intraday_parameter_plateau_models import (
    IntradayParameterPlateauAnalysisRequest,
    calculate_intraday_parameter_plateau_analysis,
)
from trading_agent.intraday_parameter_plateau_variants import parameter_variants
from trading_agent.intraday_promotion_evidence import IntradayPromotionEvidencePaths
from trading_agent.intraday_research_artifacts import load_intraday_experiment_artifact
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.intraday_research_reviewer import load_intraday_review_artifact
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    StructuredHypothesisGenerator,
    load_researcher_context_input,
)
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).resolve().parents[1]
NEW_YORK = ZoneInfo("America/New_York")
SOURCE_CONTEXT = PROJECT / "examples/research/researcher-context-v1.json"
RESPONSE = PROJECT / "examples/research/researcher-response-fixture-v1.json"
FOUNDATION = PROJECT / "examples/data/us-vwap-reclaim-historical-fixture-v1.json"
SESSION = dt.date(2026, 7, 28)
OBSERVED_AT = dt.datetime(2026, 7, 28, 20, tzinfo=dt.UTC)
type _PromotionArtifact = (
    IntradayActualResearchAuditArtifact
    | EqualRiskComparisonArtifact
    | IntradayOverfitDiagnosticsArtifact
    | IntradayParameterPlateauArtifact
    | BrokerShadowEvidenceArtifact
    | AlpacaSipEntitlementAdmissionArtifact
)


def test_source_bound_generated_candidate_waits_then_transitions_exactly_once(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    root = tmp_path.resolve()
    bars = _write_mature_bars(root / "bars.csv")
    response = _mature_response()
    ledger = ExperimentLedgerStore(root / "experiment.sqlite3")
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    receipts = ResearcherReceiptStore(root / "receipts")
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            StructuredHypothesisGenerator(
                FixtureLlmProposalClient(response),
                receipts,
                lambda: dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
            ),
            DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(
            ledger,
            receipts,
            GeneratedStrategyArtifactStore(root / "strategies", runtime),
        ),
        ResearcherPipelineArtifacts(root / "manifests", root / "queue"),
    )
    cycle = run_autonomous_research_cycle(
        AutonomousResearchCycleConfig(
            source=load_researcher_context_input(SOURCE_CONTEXT),
            pipeline=pipeline,
            ledger=ledger,
            sandbox=GeneratedStrategySandbox(
                runtime,
                root / "tasks",
                GeneratedStrategyLimits(),
            ),
            input_csv=bars,
            data_foundation_manifest=FOUNDATION,
            experiment_root=root / "experiments",
            review_root=root / "reviews",
            max_bars=100,
            max_sessions=30,
        )
    )
    artifact = cycle.accepted.strategy_artifact.artifact
    strategy_version = f"generated-python:{artifact.artifact_id}"
    experiment = load_intraday_experiment_artifact(
        root
        / "experiments"
        / f"intraday_walk_forward_{cycle.historical.experiment_artifact_id}.json"
    )
    review = load_intraday_review_artifact(
        root
        / "reviews"
        / f"intraday_research_review_{cycle.historical.review_artifact_id}.json"
    )
    assert cycle.historical.decision is IntradayReviewerDecision.PROMOTE

    evidence = _promotion_evidence(
        root / "promotion-evidence",
        strategy_version,
        artifact.payload.source_sha256,
        experiment,
        review.artifact_id,
    )
    _advance_to_challenger(
        ledger,
        strategy_version,
        experiment.artifact_id,
        review.artifact_id,
        evidence.diagnostics.name.split("_")[-1].removesuffix(".json"),
    )
    common = _promotion_arguments(ledger.path, evidence)
    assessment_root = root / "assessments"
    assert (
        promotion_cli.main(
            (
                "assess",
                *common,
                "--output-dir",
                str(assessment_root),
                "--timestamp",
                (OBSERVED_AT + dt.timedelta(minutes=10)).isoformat(),
            )
        )
        == 0
    )
    pending = json.loads(capsys.readouterr().out)
    assert pending["result"] == "manual_approval_pending"
    assert pending["blockers"] == ["manual_approval_required"]
    assert pending["authority_bindings_created"] == 0
    assert pending["lifecycle_events_created"] == 0
    assessment = next(assessment_root.glob("intraday_promotion_assessment_*.json"))
    before = ExperimentLedgerReader(ledger.path)
    assert len(before.research_sources()) == 2
    assert len(before.strategy_authority_bindings()) == 0
    assert before.lifecycle_events(strategy_version)[-1].event.to_state is StrategyLifecycleState.CHALLENGER

    assert (
        promotion_cli.main(
            (
                "assess",
                *common,
                "--output-dir",
                str(assessment_root),
                "--timestamp",
                (OBSERVED_AT + dt.timedelta(minutes=10)).isoformat(),
            )
        )
        == 0
    )
    replayed_pending = json.loads(capsys.readouterr().out)
    assert replayed_pending["artifact_created"] == 0
    approval_root = root / "approvals"
    approved_at = OBSERVED_AT + dt.timedelta(minutes=20)
    assert (
        promotion_cli.main(
            (
                "approve",
                "--assessment",
                str(assessment),
                "--approver",
                "operator_1",
                "--output-dir",
                str(approval_root),
                "--timestamp",
                approved_at.isoformat(),
            )
        )
        == 0
    )
    _ = capsys.readouterr()
    approval = next(approval_root.glob("intraday_promotion_approval_*.json"))
    control = (
        "control",
        *common,
        "--assessment",
        str(assessment),
        "--approval",
        str(approval),
        "--timestamp",
        (approved_at + dt.timedelta(minutes=10)).isoformat(),
    )
    assert promotion_cli.main(control) == 0
    first = json.loads(capsys.readouterr().out)
    assert promotion_cli.main(control) == 0
    replay = json.loads(capsys.readouterr().out)
    assert (first["authority_bindings_created"], first["lifecycle_events_created"]) == (1, 1)
    assert (replay["authority_bindings_created"], replay["lifecycle_events_created"]) == (0, 0)
    assert first["broker_mutations"] == first["order_authority_mutations"] == 0


def _write_mature_bars(path: Path) -> Path:
    sessions: list[dt.date] = []
    candidate = dt.date(2026, 1, 5)
    while len(sessions) < 30:
        if candidate.weekday() < 5:
            sessions.append(candidate)
        candidate += dt.timedelta(days=1)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "timestamp",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "prior_close",
                "average_daily_volume",
                "spread_bps",
                "catalyst",
            )
        )
        for index, session in enumerate(sessions):
            opened = dt.datetime.combine(session, dt.time(9, 30), NEW_YORK)
            risk = (0.15, 0.25, 0.35, 0.45, 0.55)[index % 5]
            writer.writerow(
                (
                    opened.isoformat(),
                    "DEMO",
                    100,
                    100.2,
                    100 - risk,
                    100,
                    100_000,
                    95,
                    1_000_000,
                    10,
                    "fixture",
                )
            )
            writer.writerow(
                (
                    (opened + dt.timedelta(minutes=1)).isoformat(),
                    "DEMO",
                    100,
                    100 + 2 * risk + 0.01,
                    100,
                    100 + 2 * risk,
                    100_000,
                    95,
                    1_000_000,
                    10,
                    "fixture",
                )
            )
    return path


def _mature_response() -> bytes:
    payload = json.loads(RESPONSE.read_text(encoding="utf-8"))
    payload["strategy_source"] = (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['low'], "
        "'rationale': 'bounded promotion fixture'}\n"
        "    return Strategy()\n"
    )
    return json.dumps(payload).encode()


def _promotion_evidence(
    root: Path,
    strategy_version: str,
    code_version: str,
    experiment,
    review_artifact_id: str,
) -> IntradayPromotionEvidencePaths:
    result = experiment.payload.result
    base = _mature_candidates()
    selected = IntradayOverfitCandidateTrace(
        trial_id=experiment.payload.trial_id,
        strategy_version=strategy_version,
        experiment_artifact_id=experiment.artifact_id,
        review_artifact_id=review_artifact_id,
        trade_count=result.trade_count,
        session_dates=tuple(item.session_date for item in result.session_outcomes),
        net_session_returns=tuple(
            sum(item.net_trade_returns) for item in result.session_outcomes
        ),
    )
    sessions = selected.session_dates
    variation = (-0.004, -0.002, 0.0, 0.002, 0.004)
    alpha = base[0].model_copy(
        update={
            "session_dates": sessions,
            "net_session_returns": tuple(
                0.001 + delta for _ in range(6) for delta in variation
            ),
        }
    )
    beta = base[1].model_copy(
        update={
            "session_dates": sessions,
            "net_session_returns": tuple(
                0.002 + delta for _ in range(6) for delta in variation
            ),
        }
    )
    traces = tuple(sorted((alpha, beta, selected), key=lambda item: item.strategy_version))
    candidates = tuple(
        EqualRiskComparisonCandidate(
            trial_id=item.trial_id,
            strategy_version=item.strategy_version,
            experiment_artifact_id=item.experiment_artifact_id,
            review_artifact_id=item.review_artifact_id,
            observed_sessions=len(item.session_dates),
            trade_count=item.trade_count,
            reviewer_decision=IntradayReviewerDecision.PROMOTE,
        )
        for item in traces
    )
    comparison_payload = EqualRiskComparisonPayload(
        comparison_version=INTRADAY_EQUAL_RISK_COMPARISON_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=experiment.payload.data_version,
        manifest_sha256=experiment.payload.manifest_sha256,
        evaluator_version=experiment.payload.evaluator_version,
        side_cost_bps=result.side_cost_bps,
        candidates=candidates,
        status=EqualRiskComparisonStatus.COMPARISON_READY,
        blockers=(),
    )
    comparison = EqualRiskComparisonArtifact(
        artifact_id=_sha(comparison_payload),
        payload=comparison_payload,
    )
    statistics = calculate_intraday_overfit_statistics(
        traces,
        total_lane_historical_trials=7,
    )
    assert statistics.selected_strategy_version == strategy_version, statistics.blockers
    diagnostics_payload = IntradayOverfitDiagnosticsPayload(
        diagnostics_version=INTRADAY_OVERFIT_DIAGNOSTICS_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=experiment.payload.data_version,
        manifest_sha256=experiment.payload.manifest_sha256,
        evaluator_version=experiment.payload.evaluator_version,
        side_cost_bps=result.side_cost_bps,
        statistics=statistics,
    )
    diagnostics = IntradayOverfitDiagnosticsArtifact(
        artifact_id=_sha(diagnostics_payload),
        payload=diagnostics_payload,
    )
    variants = parameter_variants(StrategyMode.GAP_AND_GO)
    analysis = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.GAP_AND_GO,
            trial_id=experiment.payload.trial_id,
            strategy_version=strategy_version,
            experiment_artifact_id=experiment.artifact_id,
            registered_parameter_set=strategy_contract(StrategyMode.GAP_AND_GO).parameter_set,
            variants=tuple(_trace(variant, 0.01) for variant in variants),
        )
    )
    plateau_payload = IntradayParameterPlateauPayload(
        evaluator_version=INTRADAY_PARAMETER_PLATEAU_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=experiment.payload.data_version,
        manifest_sha256=experiment.payload.manifest_sha256,
        side_cost_bps=result.side_cost_bps,
        status=analysis.status,
        analyses=(analysis,),
    )
    plateau = IntradayParameterPlateauArtifact(
        artifact_id=_sha(plateau_payload),
        payload=plateau_payload,
    )
    pairs = _broker_pairs(strategy_version)
    broker_assessment = assess_broker_shadow_pairs(pairs, 0)
    broker_payload = BrokerShadowEvidence(
        evidence_version=BROKER_SHADOW_EVIDENCE_VERSION,
        strategy_version=strategy_version,
        execution_snapshot_sha256="3" * 64,
        shadow_source_sha256="4" * 64,
        reviewed_at=OBSERVED_AT,
        status=broker_assessment.status,
        pairs=pairs,
        paired_trade_count=len(pairs),
        paired_session_count=60,
        unpaired_broker_intent_count=0,
        broker_metrics=broker_assessment.broker_metrics,
        shadow_metrics=broker_assessment.shadow_metrics,
        blockers=broker_assessment.blockers,
    )
    broker = BrokerShadowEvidenceArtifact(
        artifact_id=_sha(broker_payload),
        payload=broker_payload,
    )
    sip = build_alpaca_sip_entitlement_artifact(
        config=AlpacaSipTradeStreamConfig(SESSION, "SPY"),
        assessed_at=OBSERVED_AT,
        status=AlpacaSipEntitlementAdmissionStatus.READY,
        reason=AlpacaSipEntitlementAdmissionReason.BOUNDED_COMPLETE,
        evidence_sha256="5" * 64,
    )
    audit_payload = IntradayActualResearchAuditPayload(
        run_key="generated-source-promotion",
        plan_id="6" * 64,
        research_completed_at_epoch=int(OBSERVED_AT.timestamp()),
        dataset_input_sha256=experiment.payload.data_version,
        dataset_receipt_sha256="8" * 64,
        dataset_producer_commit_sha="9" * 40,
        manifest_sha256=experiment.payload.manifest_sha256,
        strategy_code_version=code_version,
        foundation_sha256s=("a" * 64, "b" * 64, "c" * 64),
        trial_ids=tuple(item.trial_id for item in traces),
        experiment_artifact_ids=tuple(item.experiment_artifact_id for item in traces),
        review_artifact_ids=tuple(item.review_artifact_id for item in traces),
        reviewer_decisions=(IntradayReviewerDecision.PROMOTE,) * 3,
        comparison_artifact_id=comparison.artifact_id,
        comparison_status=comparison.payload.status,
        overfit_diagnostics_artifact_id=diagnostics.artifact_id,
        overfit_diagnostics_status=diagnostics.payload.statistics.status,
        parameter_plateau_artifact_id=plateau.artifact_id,
        parameter_plateau_status=plateau.payload.status,
    )
    audit = IntradayActualResearchAuditArtifact(
        artifact_id=_sha(audit_payload),
        payload=audit_payload,
    )
    return IntradayPromotionEvidencePaths(
        audit=_publish(root, "intraday_actual_research_audit", audit),
        comparison=_publish(root, "intraday_equal_risk_comparison", comparison),
        diagnostics=_publish(root, "intraday_overfit_diagnostics", diagnostics),
        plateau=_publish(root, "intraday_parameter_plateau", plateau),
        broker_shadow=_publish(root, "intraday_broker_shadow_evidence", broker),
        sip=_publish(root, "alpaca_sip_entitlement", sip),
    )


def _advance_to_challenger(
    ledger: ExperimentLedgerStore,
    strategy_version: str,
    experiment_id: str,
    review_id: str,
    diagnostics_id: str,
) -> None:
    transitions = (
        (
            dt.date(2026, 7, 23),
            dt.date(2026, 7, 24),
            StrategyLifecycleState.HISTORICAL,
            "cost_adjusted_oos_promoted",
            experiment_id,
        ),
        (
            dt.date(2026, 7, 24),
            dt.date(2026, 7, 27),
            StrategyLifecycleState.EXPERIMENTAL_SHADOW,
            "reviewer_shadow_authorized",
            review_id,
        ),
        (
            dt.date(2026, 7, 27),
            SESSION,
            StrategyLifecycleState.CHALLENGER,
            "comparison_ready",
            diagnostics_id,
        ),
    )
    previous = ledger.lifecycle_events(strategy_version)[-1]
    for sequence, (decision, effective, target, reason, evidence) in enumerate(
        transitions,
        start=2,
    ):
        event = StrategyLifecycleEvent(
            strategy_version=strategy_version,
            sequence=sequence,
            event_kind=StrategyLifecycleEventKind.TRANSITION,
            from_state=previous.event.to_state,
            to_state=target,
            policy_version="g005_controlled_research_transition_v1",
            decision_session_date=decision,
            effective_session_date=effective,
            decided_at=dt.datetime.combine(decision, dt.time(20), dt.UTC),
            evidence_keys=tuple(sorted((str(previous.event_key), evidence))),
            reason_codes=(reason,),
            previous_event_key=previous.event_key,
        )
        with ledger.writer() as writer:
            assert writer.append_lifecycle_event(event)
        previous = ledger.lifecycle_events(strategy_version)[-1]
        assert previous.event_key == strategy_lifecycle_event_key(event)


def _broker_pairs(strategy_version: str) -> tuple[BrokerShadowTradePair, ...]:
    start = dt.date(2026, 1, 1)
    return tuple(
        sorted(
            (
                BrokerShadowTradePair(
                    recommendation_id=f"recommendation-{index}",
                    session_date=start + dt.timedelta(days=index % 60),
                    symbol="SPY",
                    strategy_version=strategy_version,
                    broker_entry=100.0,
                    broker_exit=101.0,
                    shadow_entry=100.0,
                    shadow_exit=101.0,
                    broker_net_return=-0.005 if index % 10 == 0 else 0.01,
                    shadow_net_return=-0.005 if index % 10 == 0 else 0.01,
                    return_difference=0.0,
                )
                for index in range(100)
            ),
            key=lambda pair: (pair.session_date, pair.recommendation_id),
        )
    )


def _publish(root: Path, prefix: str, artifact: _PromotionArtifact) -> Path:
    identifier = artifact.artifact_id
    path = root / f"{prefix}_{identifier}.json"
    assert publish_private_immutable_text(
        path,
        canonical_experiment_ledger_json(artifact) + "\n",
    )
    return path


def _sha(payload: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()


def _promotion_arguments(
    ledger: Path,
    paths: IntradayPromotionEvidencePaths,
) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(ledger),
        "--audit",
        str(paths.audit),
        "--comparison",
        str(paths.comparison),
        "--diagnostics",
        str(paths.diagnostics),
        "--plateau",
        str(paths.plateau),
        "--broker-shadow",
        str(paths.broker_shadow),
        "--sip",
        str(paths.sip),
        "--session-date",
        SESSION.isoformat(),
    )
