from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_day_capsule_adapter import _request
from trading_agent.dashboard_projection_day_agent import project_day_agent_facade
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_decision_delivery import (
    KrDayDecisionDeliveryBatch,
    project_kr_day_decision_delivery,
)
from trading_agent.kr_day_decision_models import (
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_service import run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.signal_contract_models import FeatureValue


def test_observed_005930_watch_is_audited_visible_and_silent_on_replay(tmp_path: Path) -> None:
    # Given: the observed watch shape: high value, one related symbol, no publisher,
    # and neutral completed-bar price/volume confirmation.
    request = _observed_watch_request()
    outputs = tmp_path / "outputs"
    decisions = KrDayDecisionStore(outputs / "kr_day" / "kr-day-decisions.sqlite3")

    # When: the same completed-bar evidence is processed twice.
    first = run_kr_day_decision_tick((request,), decisions)
    replay = run_kr_day_decision_tick((request,), decisions)

    # Then: one non-actionable, reason-bearing disposition remains durable.
    assert replay == first
    assert decisions.events() == first
    event = first[0]
    assert event.status is KrDayDecisionStatus.REJECTED
    assert event.conditional_plan is None
    assert event.reason_codes == (
        KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING,
        KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING,
        KrDayDecisionReasonCode.THEME_BREADTH_MISSING,
        KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING,
    )

    projection = project_day_agent_facade(
        outputs,
        now=event.observed_at + dt.timedelta(seconds=5),
    )
    cards = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.kr.lifecycle"))
    rendered = " ".join(f"{card.label} {card.value}" for card in cards)
    assert cards and all(card.observed_at == event.observed_at for card in cards)
    assert all(reason.value in rendered for reason in event.reason_codes)
    assert "REJECTED" in rendered
    assert "ARMED" not in rendered and "ACTIVE" not in rendered

    delivery = HermesDeliveryStore(tmp_path / "hermes.sqlite3")
    with delivery.writer() as writer:
        result = project_kr_day_decision_delivery(KrDayDecisionDeliveryBatch(first, ()), writer)
    assert (result.examined, result.inserted) == (0, 0)
    assert delivery.events() == ()


def _observed_watch_request() -> KrDayCapsuleEvaluationRequest:
    request = _request()
    values = {item.name: item.value for item in request.opportunity.candidates[0].features}
    values.update(
        theme_catalyst_count="1",
        theme_publisher_count="0",
        theme_related_symbol_count="1",
        trading_value_krw="1000000000",
        volume_ratio="1.0",
    )
    candidate = request.opportunity.candidates[0].model_copy(
        update={
            "features": tuple(
                FeatureValue(name=name, value=value)
                for name, value in sorted(values.items())
            )
        }
    )
    bars = list(request.bars)
    previous = bars[-2]
    bars[-1] = bars[-1].model_copy(
        update={
            "open": previous.close,
            "high": previous.close,
            "low": previous.close,
            "close": previous.close,
            "volume": 100,
            "trading_value_krw": previous.close * 100,
        }
    )
    return request.model_copy(
        update={
            "opportunity": request.opportunity.model_copy(update={"candidates": (candidate,)}),
            "bars": tuple(bars),
        }
    )
