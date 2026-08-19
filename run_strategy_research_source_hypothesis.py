#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# uv run run_strategy_research_source_hypothesis.py --cycle-database <path> \
#   --evidence-id <sha256> --observed-at <aware-ISO-8601>
# ───────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.research_agent_cycle_models import EvidenceId
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_store_support import InvalidResearchAgentCycleStoreError
from trading_agent.researcher_pipeline import build_source_hypothesis_factory
from trading_agent.strategy_research_evidence_service import StrategyResearchEvidenceRejected

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one research-only source-bound hypothesis from the immutable cycle store."
    )
    parser.add_argument("--cycle-database", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--observed-at", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        observed_at = dt.datetime.fromisoformat(args.observed_at)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise StrategyResearchEvidenceRejected("observation_time_invalid")
        if _SHA256.fullmatch(args.evidence_id) is None:
            raise StrategyResearchEvidenceRejected("evidence_id_invalid")
        with ResearchAgentCycleStore(args.cycle_database) as store:
            artifact = build_source_hypothesis_factory(store.all_evidence).create_routed(
                EvidenceId(args.evidence_id),
                observed_at,
            )
    except (
        InvalidResearchAgentCycleStoreError,
        OSError,
        StrategyResearchEvidenceRejected,
        TypeError,
        ValueError,
    ):
        print(
            '{"broker_mutation":0,"model_calls":0,"status":"invalid"}',
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "artifact_refs": artifact.artifact_refs,
                "broker_mutation": 0,
                "hypothesis_id": artifact.hypothesis.hypothesis_id,
                "hypothesis_sha256": artifact.hypothesis.content_sha256,
                "model_calls": 0,
                "observation_id": artifact.observation.observation_id,
                "observation_sha256": artifact.observation.content_sha256,
                "owner": artifact.hypothesis.agent_id.value,
                "source_id": artifact.candidate.source_id,
                "source_sha256": artifact.candidate.source_ref.payload_sha256,
                "status": "created",
                "trading_authority": artifact.hypothesis.trading_authority,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
