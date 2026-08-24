from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import ValidationError

from trading_agent.day_agent_challenger_publisher import DayAgentGeneratedCapsulePublisher
from trading_agent.day_agent_loop_engineer import (
    DayAgentLoopServices,
    FixedDayAgentChangeAuthor,
    ProposedAgentChange,
    run_loop_engineer,
)
from trading_agent.day_agent_version_models import AgentChangeProposal
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_policy import ExplorationPolicy
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
    MarketCloseReport,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import require_generated_strategy_runtime
from trading_agent.kr_day_market_close_metrics import KrDayMarketCloseMetrics
from trading_agent.private_immutable_file import read_private_text
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_agent_cli_bindings import LoopInputBundle
from trading_agent.us_forward_shadow_artifacts import UsForwardShadowArtifactStore
from trading_agent.us_forward_shadow_services import UsForwardShadowServices

type KrDayLoopReason = Literal[
    "challenger_registered",
    "exact_replay",
    "incident_present",
    "insufficient_evidence",
    "version_authority_missing",
]


class InvalidKrDayLoopEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day loop evidence is invalid"


@dataclass(frozen=True, slots=True)
class KrDayLoopResult:
    challenger_count: Literal[0, 1]
    reason: KrDayLoopReason
    proposal: AgentChangeProposal | None


@dataclass(frozen=True, slots=True)
class KrDayLoopAuthorityPaths:
    state_root: Path
    experiment_ledger: Path

    @property
    def version_store(self) -> Path:
        return self.state_root / "day-agent-versions.sqlite3"

    @property
    def loop_inputs(self) -> Path:
        return self.state_root / "kr-day-loop-inputs.json"

    @property
    def patch_response(self) -> Path:
        return self.state_root / "kr-day-loop-patch.json"


def run_configured_kr_day_loop_engineer(
    report: MarketCloseReport,
    metrics: KrDayMarketCloseMetrics,
    policy: ExplorationPolicy,
    paths: KrDayLoopAuthorityPaths,
) -> KrDayLoopResult:
    preliminary = _preliminary_result(report, metrics)
    if preliminary is not None:
        return preliminary
    try:
        inputs = LoopInputBundle.model_validate_json(read_private_text(paths.loop_inputs))
        first_session = inputs.future_sessions[0]
        if (
            first_session.session_date != policy.payload.effective_session_date
            or first_session.calendar_snapshot_id != policy.payload.calendar_snapshot_id
            or any(
                not item.calendar_snapshot_id.startswith("calendar://official/XKRX/")
                for item in inputs.future_sessions
            )
        ):
            raise InvalidKrDayLoopEvidenceError
        runtime = require_generated_strategy_runtime(inputs.runtime)
        services = DayAgentLoopServices(
            DayAgentVersionStore(paths.version_store),
            FixedDayAgentChangeAuthor(
                ProposedAgentChange.model_validate_json(read_private_text(paths.patch_response))
            ),
            DayAgentGeneratedCapsulePublisher(
                UsForwardShadowServices(
                    ExperimentLedgerStore(paths.experiment_ledger),
                    GeneratedStrategyArtifactStore(paths.state_root / "generated-strategies", runtime),
                    UsForwardShadowArtifactStore(paths.state_root / "forward-shadow-artifacts"),
                    paths.state_root / "loop-tasks",
                ),
                inputs.proposal_template,
                inputs.replay_bars,
                inputs.future_sessions,
            ),
        )
        return run_kr_day_loop_engineer(report, metrics, policy, services)
    except InvalidKrDayLoopEvidenceError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayLoopEvidenceError from None


def run_kr_day_loop_engineer(
    report: MarketCloseReport,
    metrics: KrDayMarketCloseMetrics,
    policy: ExplorationPolicy,
    services: DayAgentLoopServices,
) -> KrDayLoopResult:
    try:
        checked_report = MarketCloseReport.model_validate(report.model_dump(mode="python"))
        checked_metrics = KrDayMarketCloseMetrics.model_validate(metrics.model_dump(mode="python"))
        checked_policy = ExplorationPolicy.model_validate(policy.model_dump(mode="python"))
        _require_canonical_lineage(checked_report, checked_metrics, checked_policy)
        preliminary = _preliminary_result(checked_report, checked_metrics)
        if preliminary is not None:
            return preliminary
        champion = services.store.reader().champion()
        if champion is None:
            raise InvalidKrDayLoopEvidenceError
        if checked_report.payload.agent_version_id != champion.version_id:
            raise InvalidKrDayLoopEvidenceError
        existing = services.store.reader().proposal_for_report(checked_report.report_id)
        proposal = run_loop_engineer(checked_report, champion, services)
        if existing is not None:
            return KrDayLoopResult(1, "exact_replay", proposal)
        return KrDayLoopResult(1, "challenger_registered", proposal)
    except InvalidKrDayLoopEvidenceError:
        raise
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayLoopEvidenceError from None


def _preliminary_result(
    report: MarketCloseReport,
    metrics: KrDayMarketCloseMetrics,
) -> KrDayLoopResult | None:
    if metrics.payload.risk_incident_ids or metrics.payload.data_incident_ids:
        return KrDayLoopResult(0, "incident_present", None)
    failure = select_kr_day_failure(metrics.payload.selection_diagnostics)
    if failure is None or metrics.payload.completed_count == 0:
        return KrDayLoopResult(0, "insufficient_evidence", None)
    if report.payload.agent_version_id is None:
        return KrDayLoopResult(0, "version_authority_missing", None)
    return None


def _require_canonical_lineage(
    report: MarketCloseReport,
    metrics: KrDayMarketCloseMetrics,
    policy: ExplorationPolicy,
) -> None:
    report_payload = report.payload
    metric_payload = metrics.payload
    policy_payload = policy.payload
    canonical_evidence = set(report_payload.watermark.source_event_ids)
    diagnostic_evidence = {
        evidence_id
        for item in metric_payload.selection_diagnostics
        for evidence_id in item.evidence_ids
    }
    if (
        report_payload.market_id is not MarketId.KR_EQUITIES
        or metric_payload.report_id != report.report_id
        or metric_payload.session_date != report_payload.session_date
        or metric_payload.revision != report_payload.revision
        or metric_payload.selection_diagnostics != report_payload.diagnostics
        or not diagnostic_evidence <= canonical_evidence
        or policy_payload.market_id is not MarketId.KR_EQUITIES
        or policy_payload.final_report_id != report.report_id
        or policy_payload.effective_session_date != metric_payload.next_review_date
        or policy_payload.effective_session_date <= report_payload.session_date
        or not policy_payload.calendar_snapshot_id.startswith("calendar://official/XKRX/")
    ):
        raise InvalidKrDayLoopEvidenceError


def select_kr_day_failure(
    diagnostics: tuple[DayDecisionDiagnostic, ...],
) -> DayDecisionDiagnostic | None:
    failures = tuple(item for item in diagnostics if item.outcome is DayDecisionOutcome.REFUTED)
    return min(
        failures,
        key=lambda item: (item.score, tuple(DayDecisionStage).index(item.stage)),
        default=None,
    )


__all__ = (
    "InvalidKrDayLoopEvidenceError",
    "KrDayLoopAuthorityPaths",
    "KrDayLoopResult",
    "run_configured_kr_day_loop_engineer",
    "run_kr_day_loop_engineer",
    "select_kr_day_failure",
)
