from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.alpaca_sip_entitlement_artifacts import (
    AlpacaSipEntitlementAdmissionArtifact,
    AlpacaSipEntitlementAdmissionStatus,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditArtifact,
)
from trading_agent.intraday_broker_shadow_models import (
    BrokerShadowEvidenceArtifact,
    BrokerShadowEvidenceStatus,
)
from trading_agent.intraday_equal_risk_comparison_models import (
    EqualRiskComparisonArtifact,
    EqualRiskComparisonStatus,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    IntradayOverfitDiagnosticsArtifact,
    IntradayOverfitDiagnosticsStatus,
)
from trading_agent.intraday_parameter_plateau_artifacts import (
    IntradayParameterPlateauArtifact,
)
from trading_agent.intraday_parameter_plateau_models import (
    IntradayParameterPlateauStatus,
)
from trading_agent.intraday_promotion_store import load_canonical_artifact
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.us_equity_calendar import NEW_YORK

MIN_DEFLATED_SHARPE_PROBABILITY: Final = 0.95
MAX_PBO_PROBABILITY: Final = 0.05


@dataclass(frozen=True, slots=True)
class IntradayPromotionEvidencePaths:
    audit: Path
    comparison: Path
    diagnostics: Path
    plateau: Path
    broker_shadow: Path
    sip: Path


@dataclass(frozen=True, slots=True)
class VerifiedIntradayPromotionEvidence:
    strategy_version: str
    evidence_keys: tuple[str, ...]
    observed_at: tuple[dt.datetime, ...]
    blockers: tuple[str, ...]
    trial_id: str = ""
    experiment_artifact_id: str = ""
    review_artifact_id: str = ""
    data_version: str = ""
    evaluator_version: str = ""
    strategy_code_version: str = ""


def load_intraday_promotion_evidence(
    paths: IntradayPromotionEvidencePaths,
    session_date: dt.date,
) -> VerifiedIntradayPromotionEvidence:
    audit = load_canonical_artifact(paths.audit, IntradayActualResearchAuditArtifact, "intraday_actual_research_audit")
    comparison = load_canonical_artifact(
        paths.comparison,
        EqualRiskComparisonArtifact,
        "intraday_equal_risk_comparison",
    )
    diagnostics = load_canonical_artifact(
        paths.diagnostics,
        IntradayOverfitDiagnosticsArtifact,
        "intraday_overfit_diagnostics",
    )
    plateau = load_canonical_artifact(paths.plateau, IntradayParameterPlateauArtifact, "intraday_parameter_plateau")
    broker = load_canonical_artifact(
        paths.broker_shadow,
        BrokerShadowEvidenceArtifact,
        "intraday_broker_shadow_evidence",
    )
    sip = load_canonical_artifact(paths.sip, AlpacaSipEntitlementAdmissionArtifact, "alpaca_sip_entitlement")
    selected = diagnostics.payload.statistics.selected_strategy_version
    candidate = next(
        (item for item in comparison.payload.candidates if item.strategy_version == selected),
        None,
    )
    analysis = next(
        (item for item in plateau.payload.analyses if item.strategy_version == selected),
        None,
    )
    statistics = diagnostics.payload.statistics
    linked = (
        audit.payload.comparison_artifact_id == comparison.artifact_id
        and audit.payload.overfit_diagnostics_artifact_id == diagnostics.artifact_id
        and audit.payload.parameter_plateau_artifact_id == plateau.artifact_id
        and comparison.payload.data_version == diagnostics.payload.data_version == plateau.payload.data_version
        and comparison.payload.manifest_sha256 == diagnostics.payload.manifest_sha256 == plateau.payload.manifest_sha256
        and comparison.payload.evaluator_version == diagnostics.payload.evaluator_version
    )
    blockers: list[str] = []
    if not linked:
        blockers.append("evidence_identity_mismatch")
    if comparison.payload.status is not EqualRiskComparisonStatus.COMPARISON_READY:
        blockers.append("equal_risk_comparison_not_ready")
    if candidate is None or candidate.reviewer_decision is not IntradayReviewerDecision.PROMOTE:
        blockers.append("cost_adjusted_oos_not_promoted")
    if statistics.status is not IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY:
        blockers.append("dsr_pbo_not_ready")
    if (
        statistics.deflated_sharpe_probability is None
        or statistics.deflated_sharpe_probability < MIN_DEFLATED_SHARPE_PROBABILITY
    ):
        blockers.append("deflated_sharpe_below_policy")
    if statistics.pbo_probability is None or statistics.pbo_probability > MAX_PBO_PROBABILITY:
        blockers.append("pbo_above_policy")
    if plateau.payload.status is not IntradayParameterPlateauStatus.PLATEAU_READY or analysis is None:
        blockers.append("parameter_plateau_not_ready")
    if broker.payload.status is not BrokerShadowEvidenceStatus.READY or broker.payload.strategy_version != selected:
        blockers.append("broker_shadow_not_ready")
    if sip.status is not AlpacaSipEntitlementAdmissionStatus.READY:
        blockers.append("sip_validation_not_ready")
    observed_at = (
        dt.datetime.fromtimestamp(audit.payload.research_completed_at_epoch, tz=dt.UTC),
        comparison.payload.reviewed_at,
        diagnostics.payload.reviewed_at,
        plateau.payload.reviewed_at,
        broker.payload.reviewed_at,
        sip.assessed_at,
    )
    if sip.market_date != session_date or any(
        value.astimezone(NEW_YORK).date() != session_date for value in observed_at
    ):
        blockers.append("stale_evidence")
    keys = tuple(
        sorted(
            (
                audit.artifact_id,
                comparison.artifact_id,
                diagnostics.artifact_id,
                plateau.artifact_id,
                broker.artifact_id,
                sip.artifact_id,
            )
        )
    )
    return VerifiedIntradayPromotionEvidence(
        strategy_version="" if selected is None else selected,
        evidence_keys=keys,
        observed_at=observed_at,
        blockers=tuple(sorted(set(blockers))),
        trial_id="" if candidate is None else candidate.trial_id,
        experiment_artifact_id=(
            "" if candidate is None else candidate.experiment_artifact_id
        ),
        review_artifact_id="" if candidate is None else candidate.review_artifact_id,
        data_version=comparison.payload.data_version,
        evaluator_version=comparison.payload.evaluator_version,
        strategy_code_version=audit.payload.strategy_code_version,
    )


__all__ = (
    "MAX_PBO_PROBABILITY",
    "MIN_DEFLATED_SHARPE_PROBABILITY",
    "IntradayPromotionEvidencePaths",
    "VerifiedIntradayPromotionEvidence",
    "load_intraday_promotion_evidence",
)
