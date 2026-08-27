from __future__ import annotations

import json

from pydantic import BaseModel

from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot, KrLoopReleaseEvent


def canonical_kr_loop_snapshot_json(snapshot: KrLoopCandidateSnapshot) -> str:
    return _canonical(snapshot)


def canonical_kr_loop_release_json(event: KrLoopReleaseEvent) -> str:
    return _canonical(event)


def _canonical(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


__all__ = ("canonical_kr_loop_release_json", "canonical_kr_loop_snapshot_json")
