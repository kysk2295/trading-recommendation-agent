from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from tests.test_intraday_overfit_diagnostics import _mature_candidates
from tests.test_intraday_parameter_plateau import _trace
from trading_agent.alpaca_sip_entitlement_artifacts import (
    AlpacaSipEntitlementAdmissionReason,
    AlpacaSipEntitlementAdmissionStatus,
    build_alpaca_sip_entitlement_artifact,
)
from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamConfig
from trading_agent.daily_research_contract import strategy_contract
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
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
from trading_agent.intraday_promotion_evidence import (
    IntradayPromotionEvidencePaths,
    load_intraday_promotion_evidence,
)
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.strategy_factory import StrategyMode

SESSION = dt.date(2026, 7, 16)
OBSERVED_AT = dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC)
DATA_VERSION = "1" * 64
MANIFEST = "2" * 64
SELECTED = "gamma-v2"


@dataclass(frozen=True, slots=True)
class _Artifacts:
    paths: IntradayPromotionEvidencePaths
    audit: IntradayActualResearchAuditArtifact


@dataclass(frozen=True, slots=True)
class _Publication:
    prefix: str
    identifier: str
    artifact: BaseModel


def test_real_canonical_artifacts_satisfy_every_automatic_gate(tmp_path: Path) -> None:
    # Given: six independently validated private canonical artifacts
    artifacts = _artifacts(tmp_path)

    # When: the promotion evidence boundary loads and cross-checks them
    verified = load_intraday_promotion_evidence(artifacts.paths, SESSION)

    # Then: the selected cost-adjusted candidate has no automatic blocker
    assert verified.strategy_version == SELECTED
    assert verified.blockers == ()
    assert len(verified.evidence_keys) == 6
    assert verified.trial_id == "trial-gamma"
    assert verified.experiment_artifact_id == "c" * 64
    assert verified.review_artifact_id == "f" * 64
    assert verified.data_version == DATA_VERSION
    assert verified.evaluator_version == "intraday_walk_forward_v2"
    assert verified.strategy_code_version == "a" * 40


def test_cross_artifact_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    # Given: a valid terminal audit replaced by a self-consistent but cross-wired audit
    artifacts = _artifacts(tmp_path)
    payload = artifacts.audit.payload.model_copy(update={"comparison_artifact_id": "f" * 64})
    mismatched = IntradayActualResearchAuditArtifact(
        artifact_id=_sha(payload),
        payload=payload,
    )
    path = _publish(
        tmp_path,
        _Publication("intraday_actual_research_audit", mismatched.artifact_id, mismatched),
    )
    paths = IntradayPromotionEvidencePaths(
        path,
        artifacts.paths.comparison,
        artifacts.paths.diagnostics,
        artifacts.paths.plateau,
        artifacts.paths.broker_shadow,
        artifacts.paths.sip,
    )

    # When: the complete chain is re-evaluated
    verified = load_intraday_promotion_evidence(paths, SESSION)

    # Then: matching fails without granting automatic eligibility
    assert "evidence_identity_mismatch" in verified.blockers


def _artifacts(root: Path) -> _Artifacts:
    candidates = tuple(
        EqualRiskComparisonCandidate(
            trial_id=item.trial_id,
            strategy_version=item.strategy_version,
            experiment_artifact_id=item.experiment_artifact_id,
            review_artifact_id=item.review_artifact_id,
            observed_sessions=20,
            trade_count=30,
            reviewer_decision=IntradayReviewerDecision.PROMOTE,
        )
        for item in _mature_candidates()
    )
    comparison_payload = EqualRiskComparisonPayload(
        comparison_version=INTRADAY_EQUAL_RISK_COMPARISON_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=DATA_VERSION,
        manifest_sha256=MANIFEST,
        evaluator_version="intraday_walk_forward_v2",
        side_cost_bps=20,
        candidates=candidates,
        status=EqualRiskComparisonStatus.COMPARISON_READY,
        blockers=(),
    )
    comparison = EqualRiskComparisonArtifact(artifact_id=_sha(comparison_payload), payload=comparison_payload)
    statistics = calculate_intraday_overfit_statistics(_mature_candidates(), total_lane_historical_trials=7)
    diagnostics_payload = IntradayOverfitDiagnosticsPayload(
        diagnostics_version=INTRADAY_OVERFIT_DIAGNOSTICS_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=DATA_VERSION,
        manifest_sha256=MANIFEST,
        evaluator_version="intraday_walk_forward_v2",
        side_cost_bps=20,
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
            trial_id="trial-gamma",
            strategy_version=SELECTED,
            experiment_artifact_id="c" * 64,
            registered_parameter_set=strategy_contract(StrategyMode.GAP_AND_GO).parameter_set,
            variants=tuple(_trace(variant, 0.01) for variant in variants),
        )
    )
    plateau_payload = IntradayParameterPlateauPayload(
        evaluator_version=INTRADAY_PARAMETER_PLATEAU_VERSION,
        reviewed_at=OBSERVED_AT,
        data_version=DATA_VERSION,
        manifest_sha256=MANIFEST,
        side_cost_bps=20,
        status=analysis.status,
        analyses=(analysis,),
    )
    plateau = IntradayParameterPlateauArtifact(artifact_id=_sha(plateau_payload), payload=plateau_payload)
    pairs = _broker_pairs()
    broker_assessment = assess_broker_shadow_pairs(pairs, 0)
    broker_payload = BrokerShadowEvidence(
        evidence_version=BROKER_SHADOW_EVIDENCE_VERSION,
        strategy_version=SELECTED,
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
    broker = BrokerShadowEvidenceArtifact(artifact_id=_sha(broker_payload), payload=broker_payload)
    sip = build_alpaca_sip_entitlement_artifact(
        config=AlpacaSipTradeStreamConfig(SESSION, "SPY"),
        assessed_at=OBSERVED_AT,
        status=AlpacaSipEntitlementAdmissionStatus.READY,
        reason=AlpacaSipEntitlementAdmissionReason.BOUNDED_COMPLETE,
        evidence_sha256="5" * 64,
    )
    audit_payload = IntradayActualResearchAuditPayload(
        run_key="promotion-fixture",
        plan_id="6" * 64,
        research_completed_at_epoch=int(OBSERVED_AT.timestamp()),
        dataset_input_sha256="7" * 64,
        dataset_receipt_sha256="8" * 64,
        dataset_producer_commit_sha="9" * 40,
        manifest_sha256=MANIFEST,
        strategy_code_version="a" * 40,
        foundation_sha256s=("a" * 64, "b" * 64, "c" * 64),
        trial_ids=("trial-alpha", "trial-beta", "trial-gamma"),
        experiment_artifact_ids=("a" * 64, "b" * 64, "c" * 64),
        review_artifact_ids=("d" * 64, "e" * 64, "f" * 64),
        reviewer_decisions=(IntradayReviewerDecision.PROMOTE,) * 3,
        comparison_artifact_id=comparison.artifact_id,
        comparison_status=comparison.payload.status,
        overfit_diagnostics_artifact_id=diagnostics.artifact_id,
        overfit_diagnostics_status=diagnostics.payload.statistics.status,
        parameter_plateau_artifact_id=plateau.artifact_id,
        parameter_plateau_status=plateau.payload.status,
    )
    audit = IntradayActualResearchAuditArtifact(artifact_id=_sha(audit_payload), payload=audit_payload)
    return _Artifacts(
        paths=IntradayPromotionEvidencePaths(
            _publish(root, _Publication("intraday_actual_research_audit", audit.artifact_id, audit)),
            _publish(root, _Publication("intraday_equal_risk_comparison", comparison.artifact_id, comparison)),
            _publish(root, _Publication("intraday_overfit_diagnostics", diagnostics.artifact_id, diagnostics)),
            _publish(root, _Publication("intraday_parameter_plateau", plateau.artifact_id, plateau)),
            _publish(root, _Publication("intraday_broker_shadow_evidence", broker.artifact_id, broker)),
            _publish(root, _Publication("alpaca_sip_entitlement", sip.artifact_id, sip)),
        ),
        audit=audit,
    )


def _broker_pairs() -> tuple[BrokerShadowTradePair, ...]:
    start = dt.date(2026, 1, 1)
    return tuple(
        sorted(
            (
                BrokerShadowTradePair(
                    recommendation_id=f"recommendation-{index}",
                    session_date=start + dt.timedelta(days=index % 60),
                    symbol="SPY",
                    strategy_version=SELECTED,
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


def _publish(root: Path, publication: _Publication) -> Path:
    path = root / f"{publication.prefix}_{publication.identifier}.json"
    assert publish_private_immutable_text(path, canonical_experiment_ledger_json(publication.artifact) + "\n")
    return path


def _sha(payload: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
