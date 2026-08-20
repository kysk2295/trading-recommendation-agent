from __future__ import annotations

import datetime as dt
import importlib

import pytest
from pydantic import ValidationError

from tests.day_forward_trial_support import arbitrary_trial
from trading_agent.day_forward_probe_admission import (
    ForwardProbeQueueItem,
    ForwardProbeSlotRequest,
    select_active_probe_slots,
)
from trading_agent.research_identity_models import MarketId


def test_forward_probe_admission_public_contract_exists() -> None:
    # Given: the shared Day foundation package.
    module = importlib.import_module("trading_agent.day_forward_probe_admission")

    # When: the bounded slot admission contract is inspected.
    names = {
        "ForwardProbeQueueItem",
        "ForwardProbeSlotRequest",
        "ForwardProbeSlotSelection",
        "select_active_probe_slots",
    }

    # Then: the deterministic queue API is available.
    assert names <= set(module.__all__)


def _item(index: int, priority: int, market_id: MarketId) -> ForwardProbeQueueItem:
    trial = arbitrary_trial(index, market_id)
    return ForwardProbeQueueItem(
        trial=trial,
        policy_priority=priority,
        queued_at=trial.preregistered_at + dt.timedelta(seconds=index),
    )


def test_slot_selection_is_bounded_and_queue_remains_unbounded() -> None:
    # Given: seven unrelated capsule hypotheses in deliberately reversed policy order.
    candidates = tuple(
        _item(index, 6 - index, MarketId.US_EQUITIES)
        for index in range(7)
    )
    request = ForwardProbeSlotRequest(
        market_id=MarketId.US_EQUITIES,
        candidates=candidates,
        active_capsule_ids=(),
    )

    # When: the market-local slot policy is applied.
    selection = select_active_probe_slots(request)

    # Then: only three capsules activate and every other category-free candidate stays queued.
    assert tuple(item.policy_priority for item in selection.selected) == (0, 1, 2)
    assert tuple(item.policy_priority for item in selection.queued) == (3, 4, 5, 6)
    assert len(selection.active_capsule_ids) == 3


def test_existing_active_capsules_consume_market_slots() -> None:
    # Given: two already-active capsules and three new candidates.
    candidates = tuple(
        _item(index, index, MarketId.US_EQUITIES)
        for index in range(3)
    )
    request = ForwardProbeSlotRequest(
        market_id=MarketId.US_EQUITIES,
        candidates=candidates,
        active_capsule_ids=("e" * 64, "f" * 64),
    )

    # When: admission fills the remaining capacity.
    selection = select_active_probe_slots(request)

    # Then: one new capsule activates and two remain deterministically queued.
    assert selection.selected == (candidates[0],)
    assert selection.queued == candidates[1:]
    assert len(selection.active_capsule_ids) == 3


def test_us_and_kr_slots_are_independent() -> None:
    # Given: four candidates for each market.
    us = tuple(_item(index, index, MarketId.US_EQUITIES) for index in range(4))
    kr = tuple(_item(index + 10, index, MarketId.KR_EQUITIES) for index in range(4))

    # When: each market runs the same bounded policy independently.
    us_selection = select_active_probe_slots(
        ForwardProbeSlotRequest(
            market_id=MarketId.US_EQUITIES,
            candidates=us,
            active_capsule_ids=(),
        )
    )
    kr_selection = select_active_probe_slots(
        ForwardProbeSlotRequest(
            market_id=MarketId.KR_EQUITIES,
            candidates=kr,
            active_capsule_ids=(),
        )
    )

    # Then: each market receives its own three slots without cross-market sharing.
    assert tuple(item.trial.market_id for item in us_selection.selected) == (
        MarketId.US_EQUITIES,
    ) * 3
    assert tuple(item.trial.market_id for item in kr_selection.selected) == (
        MarketId.KR_EQUITIES,
    ) * 3


def test_cross_market_candidate_and_slot_limit_above_three_are_rejected() -> None:
    # Given: one KR candidate submitted to a US request.
    candidate = _item(1, 0, MarketId.KR_EQUITIES)

    # When/Then: both cross-market admission and an expanded slot budget fail closed.
    with pytest.raises(ValidationError, match="forward_probe_candidate_market_mismatch"):
        ForwardProbeSlotRequest(
            market_id=MarketId.US_EQUITIES,
            candidates=(candidate,),
            active_capsule_ids=(),
        )
    with pytest.raises(ValidationError):
        ForwardProbeSlotRequest(
            market_id=MarketId.KR_EQUITIES,
            candidates=(candidate,),
            active_capsule_ids=(),
            max_active_slots=4,
        )
