from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from trading_agent.strategy_lab_models import LabEvidenceBatch, StrategyLabProtocol, StrategyLabTraceNode


def canonical_strategy_lab_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def strategy_lab_protocol_id(protocol: StrategyLabProtocol) -> str:
    return _sha256(protocol.body)


def strategy_lab_node_id(node: StrategyLabTraceNode) -> str:
    return _sha256(node.body)


def strategy_lab_evidence_sha256(batch: LabEvidenceBatch) -> str:
    return _sha256(batch)


def strategy_lab_hypothesis_id(
    lab_id: str,
    dataset_id: str,
    parent_node_id: str | None,
    adaptation: str,
) -> str:
    encoded = json.dumps(
        (lab_id, dataset_id, parent_node_id, adaptation),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_strategy_lab_json(model).encode()).hexdigest()
