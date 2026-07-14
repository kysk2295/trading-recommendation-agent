from __future__ import annotations

import datetime as dt
from typing import Final

from trading_agent.kis_live import NEW_YORK
from trading_agent.models import RecommendationState
from trading_agent.store import PaperStore

CAUSALITY_EXCLUSION_NOTE: Final = "알림 이전에 시작한 봉의 체결 이벤트로 인과성 성과 제외"


def first_eligible_bar_at(created_at: dt.datetime) -> dt.datetime:
    local_created_at = created_at.astimezone(NEW_YORK)
    minute_start = local_created_at.replace(second=0, microsecond=0)
    if local_created_at == minute_start:
        return minute_start
    return minute_start + dt.timedelta(minutes=1)


def exclude_backdated_recommendations(
    store: PaperStore,
    observed_at: dt.datetime,
) -> int:
    excluded = 0
    recommendations = (
        row for row in store.recommendations() if row.state is not RecommendationState.CAUSALITY_EXCLUDED
    )
    for recommendation in recommendations:
        eligible_at = first_eligible_bar_at(recommendation.created_at)
        state_events = store.events(recommendation.recommendation_id)[1:]
        if not any(event.occurred_at.astimezone(NEW_YORK) < eligible_at for event in state_events):
            continue
        store.set_state(
            recommendation.recommendation_id,
            RecommendationState.CAUSALITY_EXCLUDED,
            observed_at,
            None,
            CAUSALITY_EXCLUSION_NOTE,
        )
        excluded += 1
    return excluded
