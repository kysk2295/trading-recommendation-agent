#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# uv run run_strategy_research_cycle.py --cycle-database <path> --ledger-database <path> \
#   --evidence-id <sha256> --observed-at <aware-ISO-8601> --fixture-wiring-only
# ───────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_agent_cycle_models import EvidenceId
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_store_support import InvalidResearchAgentCycleStoreError
from trading_agent.researcher_pipeline import build_source_hypothesis_factory
from trading_agent.strategy_research_evidence_service import StrategyResearchEvidenceRejected
from trading_agent.strategy_research_experiment_models import AttemptSpec, ParameterValue, ScienceExperiment
from trading_agent.strategy_research_holdout_reviewer import (
    HoldoutBranch,
    SealedHoldoutPayload,
    SealedHoldoutReviewer,
)
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_science_kernel import ScienceKernel
from trading_agent.strategy_research_types import (
    AttemptStatus,
    EvidenceKind,
    EvidenceUse,
    LiveEligibilityPolicy,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one fixture-wiring-only Science Kernel vertical from immutable cycle-store evidence."
    )
    parser.add_argument("--cycle-database", type=Path, required=True)
    parser.add_argument("--ledger-database", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--fixture-wiring-only", action="store_true", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observed_at = dt.datetime.fromisoformat(args.observed_at)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise StrategyResearchEvidenceRejected("observation_time_invalid")
        if _SHA256.fullmatch(args.evidence_id) is None:
            raise StrategyResearchEvidenceRejected("evidence_id_invalid")
        with ResearchAgentCycleStore(args.cycle_database) as cycle_store:
            artifact = build_source_hypothesis_factory(cycle_store.all_evidence).create_routed(
                EvidenceId(args.evidence_id), observed_at
            )
        parameter_values = (ParameterValue(name="rank_cutoff", value=0.1),)
        holdout = SealedHoldoutPayload(
            reviewer_id="fixture-independent-reviewer-v1",
            branches=(
                HoldoutBranch(
                    parameter_values=parameter_values,
                    values=tuple(0.001 if index % 2 else -0.001 for index in range(40)),
                    cluster_keys=tuple(f"session-{index // 5}" for index in range(40)),
                ),
            ),
        )
        hypothesis = _fixture_hypothesis(artifact.hypothesis, holdout.content_sha256)
        experiment = ScienceExperiment(
            started_at=observed_at + dt.timedelta(seconds=1),
            attempts=(
                AttemptSpec(
                    parameter_values=parameter_values,
                    status=AttemptStatus.SUCCEEDED,
                    train_values=(-0.001, 0.0, 0.001),
                    validation_values=(0.001, -0.001, 0.0),
                    error_class=None,
                    elapsed_cpu_seconds=1,
                ),
            ),
        )
        result = ScienceKernel(
            ExperimentLedgerStore(args.ledger_database),
            SealedHoldoutReviewer.from_payload(holdout),
        ).run(hypothesis, experiment)
    except (
        InvalidResearchAgentCycleStoreError,
        OSError,
        StrategyResearchEvidenceRejected,
        StrategyResearchLedgerError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "broker_mutation": 0,
                    "error_class": type(error).__name__,
                    "reason": str(error),
                    "profitability_claim": False,
                    "status": "invalid",
                    "trading_authority": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "attempt_ids": result.attempt_ids,
                "broker_mutation": 0,
                "evidence_use": hypothesis.evidence_use.value,
                "feedback_result_id": result.feedback_result_id,
                "holdout_reveal_id": result.holdout_reveal_id,
                "hypothesis_id": result.hypothesis_id,
                "outcome": result.terminal.outcome.value,
                "owner": result.owner_agent_id.value,
                "profitability_claim": result.terminal.profitability_claim,
                "protocol_id": result.protocol_id,
                "selected_attempt_id": result.selected_attempt_id,
                "source_id": result.source_ids[0],
                "status": "terminal",
                "terminal_result_id": result.terminal.result_id,
                "trading_authority": result.terminal.trading_authority,
                "wiring_only": True,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _fixture_hypothesis(source: ImmutableHypothesis, holdout_commitment_sha256: str) -> ImmutableHypothesis:
    refs = tuple(
        item.model_copy(
            update={
                "source_kind": EvidenceKind.FIXTURE,
                "evidence_use": EvidenceUse.WIRING_ONLY,
                "live_eligibility_policy": LiveEligibilityPolicy.WIRING_ONLY_NO_LIVE_USE,
            }
        )
        for item in source.source_refs
    )
    observation = source.observation.model_copy(update={"source_refs": refs})
    seal = source.holdout_period_sealed_ref.model_copy(update={"commitment_sha256": holdout_commitment_sha256})
    return ImmutableHypothesis.model_validate(
        source.model_dump(mode="python")
        | {
            "evidence_use": EvidenceUse.WIRING_ONLY,
            "observation": observation,
            "source_refs": refs,
            "holdout_period_sealed_ref": seal,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
