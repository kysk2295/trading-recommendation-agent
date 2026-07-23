from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from tests.challenger_replay_fixtures import write_closed_source_session
from trading_agent.data_capability_models import DataEntitlement
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ResearchSourceKind
from trading_agent.intraday_research_dataset import materialize_intraday_research_dataset
from trading_agent.intraday_research_dataset_models import (
    IntradayResearchDatasetRequest,
    IntradayResearchDatasetResult,
)
from trading_agent.lane_identity_models import LaneId
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.source_driven_hypothesis_queue import publish_source_driven_hypothesis_queue
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
    SourceDrivenHypothesisQueueArtifact,
    SourceDrivenHypothesisQueueItem,
    SourceDrivenHypothesisQueueSnapshot,
)

NOW = dt.datetime(2026, 7, 23, 5, 30, tzinfo=dt.UTC)
PROJECT = Path(__file__).resolve().parents[1]
KIS_ENTITLEMENT = (
    PROJECT / "examples" / "data" / "kis-us-candidate-minute-historical-research-v1.json"
)


def write_dataset(tmp_path: Path) -> IntradayResearchDatasetResult:
    source = tmp_path / "source"
    write_closed_source_session(source, include_censored_symbol=False)
    return materialize_intraday_research_dataset(
        IntradayResearchDatasetRequest(
            session_dirs=(source,),
            output_root=tmp_path / "dataset",
            max_sessions=1,
            max_bars=500,
        )
    )


def write_entitlement(tmp_path: Path, *, provider: str = "kis") -> Path:
    payload = json.loads(KIS_ENTITLEMENT.read_text(encoding="utf-8"))
    if provider != "kis":
        payload["entitlement_id"] = f"{provider}-strict-forward-research-v1"
        payload["source_id"]["provider"] = provider
    entitlement = DataEntitlement.model_validate(payload)
    path = tmp_path / f"{provider}-entitlement.json"
    payload = json.dumps(entitlement.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    assert publish_private_immutable_text(path, payload)
    return path


def write_queue(
    tmp_path: Path,
    cards: tuple[tuple[str, str], ...] = (("H-MOM-VWAP-ACTUAL-001", "a" * 64),),
) -> tuple[Path, tuple[str, ...]]:
    items = tuple(
        SourceDrivenHypothesisQueueItem(
            card_key=card_key,
            hypothesis_id=hypothesis_id,
            lane_id=LaneId.INTRADAY_MOMENTUM,
            registered_at=NOW - dt.timedelta(minutes=2),
            hypothesis=f"Actual strict forward sessions may support bounded trial {index}.",
            falsification_rule="Reject when registered cost-adjusted out-of-sample evidence fails.",
            economic_mechanism="Delayed participation may support a same-day continuation.",
            counterfactual_baseline="Matched eligible sessions without the registered setup.",
            source_keys=(f"{index + 4:x}" * 64,),
            source_kinds=(ResearchSourceKind.INTERNAL_OBSERVATION,),
            strategy_versions=(),
            historical_trial_ids=(),
            route=HypothesisQueueRoute.STRATEGY_DESIGN,
        )
        for index, (hypothesis_id, card_key) in enumerate(cards)
    )
    snapshot = SourceDrivenHypothesisQueueSnapshot(
        as_of=NOW - dt.timedelta(minutes=1),
        items=items,
    )
    snapshot_id = hashlib.sha256(canonical_experiment_ledger_json(snapshot).encode()).hexdigest()
    artifact = SourceDrivenHypothesisQueueArtifact(snapshot_id=snapshot_id, snapshot=snapshot)
    path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", artifact)
    return path, tuple(card_key for _, card_key in cards)


__all__ = ("NOW", "write_dataset", "write_entitlement", "write_queue")
