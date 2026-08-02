from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from trading_agent.research_agent_cycle_models import ResearchAgentCycleV1
from trading_agent.research_agent_systematic_input_evidence import (
    verify_systematic_input_evidence_graph,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
    load_systematic_input_activation,
)

if TYPE_CHECKING:
    from trading_agent.research_agent_systematic import SystematicResearchActionConfig


def resolve_ready_systematic_input(
    activation_path: Path,
) -> ReadySystematicInputActivation:
    activation = load_systematic_input_activation(activation_path)
    match activation:
        case BlockedSystematicInputActivation():
            raise InvalidSystematicInputActivationError("activation_blocked")
        case ReadySystematicInputActivation() as ready:
            artifact_root = Path(
                os.path.commonpath(
                    (
                        ready.input_csv_path,
                        ready.dataset_receipt_path,
                        ready.catalog_receipt_path,
                        ready.input_binding_receipt_path,
                        ready.foundation_path,
                    )
                )
            )
            facts = verify_systematic_input_evidence_graph(artifact_root)
            verified = ReadySystematicInputActivation(
                input_csv_path=facts.input_csv_path,
                input_csv_sha256=facts.input_csv_sha256,
                dataset_receipt_path=facts.dataset_receipt_path,
                dataset_receipt_sha256=facts.dataset_receipt_sha256,
                catalog_receipt_path=facts.catalog_receipt_path,
                catalog_receipt_sha256=facts.catalog_receipt_sha256,
                input_binding_receipt_path=facts.input_binding_receipt_path,
                input_binding_receipt_sha256=facts.input_binding_receipt_sha256,
                foundation_path=facts.foundation_path,
                foundation_sha256=facts.foundation_sha256,
                producer_commit_sha=facts.producer_commit_sha,
                input_sha256=facts.input_sha256,
                selected_session_dates=facts.selected_session_dates,
                bar_count=facts.bar_count,
                max_sessions=facts.max_sessions,
                max_bars=facts.max_bars,
                rss_limit_gib=facts.rss_limit_gib,
                activated_at=facts.registered_at,
            )
            if ready != verified:
                raise InvalidSystematicInputActivationError("activation_graph_mismatch")
            if load_systematic_input_activation(activation_path) != ready:
                raise InvalidSystematicInputActivationError("activation_changed_during_verification")
            return ready
        case unreachable:
            assert_never(unreachable)


def systematic_cycle_command(
    config: SystematicResearchActionConfig,
    cycle: ResearchAgentCycleV1,
    ready: ReadySystematicInputActivation,
) -> tuple[str, ...]:
    provider = (
        ("--response-fixture", str(config.response_fixture))
        if config.response_fixture is not None
        else (
            "--hermes-executable",
            str(config.hermes_executable),
            "--provider-id",
            config.provider_id,
            "--model-id",
            config.model_id,
        )
    )
    return (
        str(config.uv_executable),
        "run",
        "--offline",
        "python",
        str(config.project_root / "run_autonomous_research_cycle.py"),
        "--context",
        str(config.context),
        *provider,
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--receipt-root",
        str(config.receipt_root),
        "--strategy-root",
        str(config.strategy_root),
        "--manifest-root",
        str(config.manifest_root),
        "--queue-root",
        str(config.queue_root),
        "--input-csv",
        str(ready.input_csv_path),
        "--data-foundation-manifest",
        str(ready.foundation_path),
        "--artifact-root",
        str(config.artifact_root),
        "--review-root",
        str(config.review_root),
        "--output-dir",
        str(config.runs_root / cycle.cycle_id / "output"),
        "--python-executable",
        str(config.python_executable),
        "--max-bars",
        str(config.max_bars),
        "--max-sessions",
        str(config.max_sessions),
        "--rss-limit-gib",
        str(config.rss_limit_gib),
    )


__all__ = ("resolve_ready_systematic_input", "systematic_cycle_command")
