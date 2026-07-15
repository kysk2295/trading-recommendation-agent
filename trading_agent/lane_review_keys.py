from __future__ import annotations

import hashlib
import json
from typing import NewType

from trading_agent.lane_review_models import LaneReviewEvent

LaneReviewEventKey = NewType("LaneReviewEventKey", str)


def lane_review_event_key(event: LaneReviewEvent) -> LaneReviewEventKey:
    return LaneReviewEventKey(hashlib.sha256(canonical_lane_review_json(event).encode()).hexdigest())


def canonical_lane_review_json(event: LaneReviewEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
