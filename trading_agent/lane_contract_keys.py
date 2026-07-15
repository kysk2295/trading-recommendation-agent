from __future__ import annotations

import hashlib
import json
from typing import NewType

from pydantic import BaseModel

from trading_agent.lane_contract_models import (
    ExperimentScope,
    LaneAccountBinding,
    LaneDailySnapshot,
    LaneManifest,
)

LaneManifestKey = NewType("LaneManifestKey", str)
LaneAccountBindingKey = NewType("LaneAccountBindingKey", str)
ExperimentScopeKey = NewType("ExperimentScopeKey", str)
LaneDailySnapshotKey = NewType("LaneDailySnapshotKey", str)


def lane_manifest_key(manifest: LaneManifest) -> LaneManifestKey:
    return LaneManifestKey(_model_sha256(manifest))


def lane_account_binding_key(binding: LaneAccountBinding) -> LaneAccountBindingKey:
    return LaneAccountBindingKey(_model_sha256(binding))


def experiment_scope_key(scope: ExperimentScope) -> ExperimentScopeKey:
    return ExperimentScopeKey(_model_sha256(scope))


def lane_daily_snapshot_key(snapshot: LaneDailySnapshot) -> LaneDailySnapshotKey:
    return LaneDailySnapshotKey(_model_sha256(snapshot))


def canonical_lane_contract_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_lane_contract_json(model).encode()).hexdigest()
