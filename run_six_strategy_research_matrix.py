#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# uv run python run_six_strategy_research_matrix.py --observed-at 2026-08-19T15:00:00+00:00
# ───────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Never

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_methodologies import strategy_research_methodology
from trading_agent.strategy_research_observation_builders import (
    MethodologyObservation,
    MethodologyObservationInput,
    SourceAuthorityReceipt,
    build_methodology_observation,
)
from trading_agent.strategy_research_runtime import StrategyResearchRuntime
from trading_agent.strategy_research_runtime_models import StrategyResearchWork
from trading_agent.strategy_research_runtime_source import PrivateStrategyResearchWorkSource
from trading_agent.strategy_research_types import ResearchAgentId, aware


class MatrixRunner:
    __slots__ = ()

    def run(self, work: StrategyResearchWork) -> Never:
        raise AssertionError(work.evidence_event_id)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate the deterministic credential-free six-agent wiring matrix."
    )
    parser.add_argument("--observed-at", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observed_at = dt.datetime.fromisoformat(args.observed_at)
        if not aware(observed_at):
            raise ValueError
    except ValueError:
        print(
            json.dumps(
                {
                    "broker_mutation": 0,
                    "profitability_claim": False,
                    "reason": "observation_time_invalid",
                    "status": "invalid",
                    "trading_mutation": 0,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(_matrix(observed_at), indent=2, sort_keys=True))
    return 0


def _matrix(observed_at: dt.datetime) -> dict[str, bool | int | str | list[dict[str, str | list[str] | None]]]:
    observations = {
        agent_id: _observation(agent_id, observed_at)
        for agent_id in ResearchAgentId
    }
    with tempfile.TemporaryDirectory(prefix="six-strategy-research-matrix-") as temporary:
        root = Path(temporary)
        store = ExperimentLedgerStore(root / "runtime.sqlite3")
        runtime = StrategyResearchRuntime(
            store,
            PrivateStrategyResearchWorkSource(root / "work"),
            MatrixRunner(),
        )
        status = runtime.tick(observed_at)
        reader = ExperimentLedgerReader(store.path)
        rows = []
        for slot in status.slots:
            policy = strategy_research_methodology(slot.agent_id)
            observation = observations[slot.agent_id]
            event = reader.strategy_research_agent_state(slot.agent_id)[-1]
            rows.append(
                {
                    "agent_id": slot.agent_id.value,
                    "cursor": event.last_event_id,
                    "evidence_refs": [
                        f"{authority}:fixture-{slot.agent_id.value}"
                        for authority in policy.required_source_authorities
                    ],
                    "next_maturity": observation.matures_at.isoformat(),
                    "next_test": policy.next_test_policy,
                    "policy_waiting_reason": f"source_wait:{policy.observation_grammar}",
                    "resampling": policy.resampling_method.value,
                    "runtime_reason": event.reason,
                    "state": slot.state,
                }
            )
    return {
        "atomic_batch": False,
        "broker_mutation": 0,
        "fixture_wiring_only": True,
        "heavy_cycles_started": status.heavy_cycles_started,
        "observed_at": observed_at.isoformat(),
        "profitability_claim": False,
        "runner_calls": [],
        "six_agents": rows,
        "temporary_store_cleanup": "TemporaryDirectory",
        "trading_mutation": 0,
    }


def _observation(agent_id: ResearchAgentId, observed_at: dt.datetime) -> MethodologyObservation:
    policy = strategy_research_methodology(agent_id)
    receipts = tuple(
        SourceAuthorityReceipt(
            authority,
            f"fixture-{agent_id.value}",
            observed_at,
            observed_at,
            True,
            True,
            True,
        )
        for authority in policy.required_source_authorities
    )
    return build_methodology_observation(MethodologyObservationInput(agent_id, observed_at, receipts))


if __name__ == "__main__":
    raise SystemExit(main())
