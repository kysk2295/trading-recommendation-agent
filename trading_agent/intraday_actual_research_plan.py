from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_actual_research import run_intraday_actual_research
from trading_agent.intraday_actual_research_models import (
    IntradayActualResearchPaths,
    IntradayActualResearchRequest,
    IntradayActualResearchResult,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchPlanError,
    IntradayActualResearchRunPlan,
    IntradayActualResearchRunPlanContent,
    IntradayActualResearchRunSpec,
    create_intraday_actual_research_run_plan,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.source_driven_hypothesis_queue import (
    load_source_driven_hypothesis_queue,
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)
from trading_agent.source_driven_hypothesis_queue_models import (
    InvalidSourceDrivenHypothesisQueueError,
)


@dataclass(frozen=True, slots=True)
class PlannedIntradayActualResearchResult:
    plan: IntradayActualResearchRunPlan
    plan_path: Path
    plan_created: bool
    queue_created: bool
    actual: IntradayActualResearchResult


def run_planned_intraday_actual_research(
    spec: IntradayActualResearchRunSpec,
    *,
    plan_root: Path,
    queue_root: Path,
    observed_at: dt.datetime,
) -> PlannedIntradayActualResearchResult:
    plan, plan_path, plan_created, queue_created = prepare_intraday_actual_research_plan(
        spec,
        plan_root=plan_root,
        queue_root=queue_root,
    )
    content = plan.content
    paths = content.spec.paths
    actual = run_intraday_actual_research(
        IntradayActualResearchRequest(
            session_dirs=content.spec.session_dirs,
            required_session_dates=content.spec.required_session_dates,
            strategy_bindings=content.spec.strategy_bindings,
            code_version=content.spec.code_version,
            registered_at=content.spec.registered_at,
            observed_at=observed_at,
            minimum_clean_sessions=content.spec.minimum_clean_sessions,
            minimum_training_sessions=content.spec.minimum_training_sessions,
            max_sessions=content.spec.max_sessions,
            max_bars=content.spec.max_bars,
            per_side_fee_bps=content.spec.per_side_fee_bps,
            per_side_slippage_bps=content.spec.per_side_slippage_bps,
            bootstrap_samples=content.spec.bootstrap_samples,
            rss_limit_gib=content.spec.rss_limit_gib,
            paths=IntradayActualResearchPaths(
                dataset_root=paths.dataset_root,
                binding_root=paths.binding_root,
                entitlement_contract=paths.entitlement_contract,
                source_queue_artifact=content.source_queue_artifact,
                lane_registry=paths.lane_registry,
                experiment_ledger=paths.experiment_ledger,
                artifact_root=paths.artifact_root,
                review_root=paths.review_root,
            ),
        )
    )
    return PlannedIntradayActualResearchResult(
        plan=plan,
        plan_path=plan_path,
        plan_created=plan_created,
        queue_created=queue_created,
        actual=actual,
    )


def prepare_intraday_actual_research_plan(
    spec: IntradayActualResearchRunSpec,
    *,
    plan_root: Path,
    queue_root: Path,
) -> tuple[IntradayActualResearchRunPlan, Path, bool, bool]:
    try:
        checked_spec = IntradayActualResearchRunSpec.model_validate(
            spec.model_dump(mode="python")
        )
        plan_path = intraday_actual_research_plan_path(plan_root, checked_spec.run_key)
        if plan_path.exists():
            plan = load_intraday_actual_research_plan(plan_path)
            if plan.content.spec != checked_spec:
                raise IntradayActualResearchPlanError
            queue = load_source_driven_hypothesis_queue(
                plan.content.source_queue_artifact
            )
            if queue.snapshot_id != plan.content.source_queue_snapshot_id:
                raise IntradayActualResearchPlanError
            return plan, plan_path, False, False

        queue = project_source_driven_hypothesis_queue(
            ExperimentLedgerReader(checked_spec.paths.experiment_ledger)
        )
        queue_path, queue_created = publish_source_driven_hypothesis_queue(
            queue_root,
            queue,
        )
        plan = create_intraday_actual_research_run_plan(
            IntradayActualResearchRunPlanContent(
                spec=checked_spec,
                source_queue_snapshot_id=queue.snapshot_id,
                source_queue_artifact=queue_path,
            )
        )
        created = publish_private_immutable_text(
            plan_path,
            canonical_experiment_ledger_json(plan) + "\n",
        )
        return plan, plan_path, created, queue_created
    except IntradayActualResearchPlanError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        InvalidSourceDrivenHypothesisQueueError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayActualResearchPlanError from None


def intraday_actual_research_plan_path(root: Path, run_key: str) -> Path:
    return root / f"intraday_actual_research_plan_{run_key}.json"


def load_intraday_actual_research_plan(
    path: Path,
) -> IntradayActualResearchRunPlan:
    try:
        payload = read_private_text(path)
        plan = IntradayActualResearchRunPlan.model_validate_json(payload)
        expected = intraday_actual_research_plan_path(
            path.parent,
            plan.content.spec.run_key,
        )
        if path.name != expected.name or payload != canonical_experiment_ledger_json(plan) + "\n":
            raise IntradayActualResearchPlanError
        return plan
    except IntradayActualResearchPlanError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayActualResearchPlanError from None


__all__ = (
    "IntradayActualResearchPlanError",
    "PlannedIntradayActualResearchResult",
    "intraday_actual_research_plan_path",
    "load_intraday_actual_research_plan",
    "prepare_intraday_actual_research_plan",
    "run_planned_intraday_actual_research",
)
