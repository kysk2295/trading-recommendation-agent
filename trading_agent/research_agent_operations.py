from __future__ import annotations

import datetime as dt
import json

from trading_agent import research_agent_operations_readers as readers
from trading_agent.research_agent_operations_models import (
    BoundedStorageStatus,
    InvalidResearchAgentOperationsSourceError,
    OperationsAlertReason,
    OperationsBlockedSource,
    OperationsEvaluationContext,
    OperationsSourceSnapshot,
    ResearchAgentOperationsInputs,
    ResearchAgentOperationsLimits,
    ResearchAgentOperationsStatus,
    SystematicHeavyExperimentStatus,
    evaluate_family_operations,
)


def build_research_agent_operations_status(
    inputs: ResearchAgentOperationsInputs,
    limits: ResearchAgentOperationsLimits,
) -> ResearchAgentOperationsStatus:
    try:
        cycle_path = readers.require_operations_store(inputs.cycle_database, "cycle")
        receipt_root = readers.require_operations_store(inputs.task_receipt_root, "receipt")
        runs_root = readers.require_operations_store(inputs.systematic_runs_root, "runs")
        cycle_facts = readers.read_cycle_operations_facts(cycle_path, inputs.as_of)
        receipts = readers.read_task_receipts(receipt_root)
        run_files = readers.private_store_files(runs_root, "runs")
        snapshot = OperationsSourceSnapshot(
            cycles=cycle_facts,
            receipts=receipts,
            heavy_completions=readers.heavy_experiment_completions(run_files),
            storage_bytes=readers.cycle_database_storage_bytes(cycle_path)
            + sum(path.stat().st_size for path in readers.private_store_files(receipt_root, "receipt"))
            + sum(path.stat().st_size for path in run_files),
        )
    except InvalidResearchAgentOperationsSourceError as error:
        blocked = OperationsBlockedSource(as_of=inputs.as_of, limits=limits, reason=error.reason)
        return ResearchAgentOperationsStatus.blocked_source(blocked)
    return _status(inputs.as_of, limits, snapshot)


def canonical_research_agent_operations_json(status: ResearchAgentOperationsStatus) -> str:
    return json.dumps(status.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _status(
    now: dt.datetime,
    limits: ResearchAgentOperationsLimits,
    snapshot: OperationsSourceSnapshot,
) -> ResearchAgentOperationsStatus:
    context = OperationsEvaluationContext(as_of=now, limits=limits, receipts=snapshot.receipts)
    families = tuple(evaluate_family_operations(fact, context) for fact in snapshot.cycles)
    heavy_exhausted = snapshot.heavy_completions >= limits.systematic_heavy_experiment_limit
    storage_over = snapshot.storage_bytes > limits.storage_limit_bytes
    alerts = {alert for family in families for alert in family.alerts}
    if heavy_exhausted:
        alerts.add(OperationsAlertReason.HEAVY_EXPERIMENT_BUDGET_EXHAUSTED)
    if storage_over:
        alerts.add(OperationsAlertReason.STORAGE_LIMIT_EXCEEDED)
    ordered_alerts = tuple(sorted(alerts, key=str))
    return ResearchAgentOperationsStatus(
        status="ready" if not ordered_alerts else "blocked",
        as_of=now,
        families=families,
        systematic_heavy_experiments=SystematicHeavyExperimentStatus(
            completions=snapshot.heavy_completions,
            limit=limits.systematic_heavy_experiment_limit,
            status="exhausted" if heavy_exhausted else "available",
        ),
        storage=BoundedStorageStatus(
            used_bytes=snapshot.storage_bytes,
            limit_bytes=limits.storage_limit_bytes,
            status="over_limit" if storage_over else "within_limit",
        ),
        alerts=ordered_alerts,
    )


__all__ = (
    "InvalidResearchAgentOperationsSourceError",
    "build_research_agent_operations_status",
    "canonical_research_agent_operations_json",
)
