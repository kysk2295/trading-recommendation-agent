from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_actual_research_audit_inputs import (
    load_actual_research_inputs,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditArtifact,
    IntradayActualResearchAuditError,
    IntradayActualResearchAuditPayload,
    IntradayActualResearchAuditRequest,
    IntradayActualResearchAuditResult,
)
from trading_agent.intraday_actual_research_audit_trials import (
    load_actual_research_trials,
)
from trading_agent.intraday_actual_research_plan import (
    IntradayActualResearchPlanError,
    load_intraday_actual_research_plan,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchRunPlan,
)
from trading_agent.intraday_equal_risk_comparison import (
    EqualRiskComparisonRequest,
    compare_intraday_equal_risk_trials,
)
from trading_agent.intraday_overfit_diagnostics import (
    IntradayOverfitDiagnosticsRequest,
    diagnose_intraday_overfit,
)
from trading_agent.intraday_parameter_plateau import (
    IntradayParameterPlateauRequest,
    diagnose_intraday_parameter_plateau,
)
from trading_agent.intraday_parameter_plateau_models import (
    InvalidIntradayParameterPlateauError,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

_SUCCESS_RECEIPT: Final = re.compile(r"exit_code=0\ncompleted_at_epoch=([1-9][0-9]*)\n")


def audit_intraday_actual_research(
    request: IntradayActualResearchAuditRequest,
) -> IntradayActualResearchAuditResult:
    try:
        plan = load_intraday_actual_research_plan(request.plan_path)
        _require_plan(request, plan)
        completed_at = _load_success_receipt(request.research_receipt)
        _require_ready_report(request.research_report, plan)
        dataset, binding = load_actual_research_inputs(plan)
        trials = load_actual_research_trials(plan, dataset, binding)
        reviewed_at = dt.datetime.fromtimestamp(completed_at, tz=dt.UTC)
        ledger = ExperimentLedgerReader(
            plan.content.spec.paths.experiment_ledger
        )
        comparison = (
            compare_intraday_equal_risk_trials(
                EqualRiskComparisonRequest(
                    ledger=ledger,
                    experiments=trials.experiments,
                    reviews=trials.reviews,
                    artifact_root=request.output_root,
                    reviewed_at=reviewed_at,
                )
            )[0]
            if len(trials.trial_ids) >= 2
            else None
        )
        diagnostics = (
            diagnose_intraday_overfit(
                IntradayOverfitDiagnosticsRequest(
                    ledger=ledger,
                    experiments=trials.experiments,
                    reviews=trials.reviews,
                    artifact_root=request.output_root,
                    reviewed_at=reviewed_at,
                )
            )[0]
            if len(trials.trial_ids) == 3
            else None
        )
        plateau = diagnose_intraday_parameter_plateau(
            IntradayParameterPlateauRequest(
                ledger=ledger,
                manifest=binding.manifest,
                bars=dataset.bars,
                experiments=trials.experiments,
                artifact_root=request.output_root,
                reviewed_at=reviewed_at,
            )
        )[0]
        payload = IntradayActualResearchAuditPayload(
            run_key=request.run_key,
            plan_id=plan.plan_id,
            research_completed_at_epoch=completed_at,
            dataset_input_sha256=dataset.input_sha256,
            dataset_receipt_sha256=dataset.receipt_sha256,
            dataset_producer_commit_sha=dataset.producer_commit_sha,
            manifest_sha256=binding.manifest_sha256,
            strategy_code_version=binding.manifest.code_version,
            foundation_sha256s=binding.foundation_sha256s,
            trial_ids=trials.trial_ids,
            experiment_artifact_ids=trials.experiment_artifact_ids,
            review_artifact_ids=trials.review_artifact_ids,
            reviewer_decisions=trials.reviewer_decisions,
            comparison_artifact_id=(None if comparison is None else comparison.artifact_id),
            comparison_status=(None if comparison is None else comparison.payload.status),
            overfit_diagnostics_artifact_id=(
                None if diagnostics is None else diagnostics.artifact_id
            ),
            overfit_diagnostics_status=(
                None
                if diagnostics is None
                else diagnostics.payload.statistics.status
            ),
            parameter_plateau_artifact_id=plateau.artifact_id,
            parameter_plateau_status=plateau.payload.status,
        )
        artifact_id = _sha(canonical_experiment_ledger_json(payload))
        artifact = IntradayActualResearchAuditArtifact(
            artifact_id=artifact_id,
            payload=payload,
        )
        artifact_path = request.output_root / f"intraday_actual_research_audit_{artifact.artifact_id}.json"
        created = publish_private_immutable_text(
            artifact_path,
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return IntradayActualResearchAuditResult(
            artifact=artifact,
            artifact_path=artifact_path,
            created=created,
        )
    except IntradayActualResearchAuditError:
        raise
    except (
        IntradayActualResearchPlanError,
        InvalidIntradayParameterPlateauError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayActualResearchAuditError("invalid_terminal_evidence") from None


def _require_plan(
    request: IntradayActualResearchAuditRequest,
    plan: IntradayActualResearchRunPlan,
) -> None:
    spec = plan.content.spec
    if (
        spec.run_key != request.run_key
        or spec.dataset_producer_commit_sha != request.expected_dataset_producer_commit_sha
        or spec.code_version != request.expected_code_version
    ):
        raise IntradayActualResearchAuditError("plan_identity_mismatch")


def _load_success_receipt(path: Path) -> int:
    match = _SUCCESS_RECEIPT.fullmatch(read_private_text(path))
    if match is None:
        raise IntradayActualResearchAuditError("research_receipt_not_successful")
    return int(match.group(1))


def _require_ready_report(path: Path, plan: IntradayActualResearchRunPlan) -> None:
    lines = read_private_text(path).splitlines()
    markers = (
        "- result: ready",
        f"- run key: {plan.content.spec.run_key}",
        f"- plan id: {plan.plan_id}",
    )
    if any(lines.count(marker) != 1 for marker in markers):
        raise IntradayActualResearchAuditError("research_report_not_ready")


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ("audit_intraday_actual_research",)
