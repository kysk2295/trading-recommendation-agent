from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_kr_theme_day_signal import OBSERVED, _opportunity
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_same_cycle_delivery import (
    InvalidKrSameCycleDeliveryError,
    KrSameCycleDeliveryRequest,
    project_kr_same_cycle_delivery,
)
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot

CYCLE_ID = "kr-same-cycle-delivery-001"
PROJECTED_AT = OBSERVED


def test_current_cycle_opportunity_projects_watch_once(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    request = _request((_cycle_opportunity(),))

    # When
    first = project_kr_same_cycle_delivery(store, request)
    replay = project_kr_same_cycle_delivery(store, request)

    # Then
    assert first.inserted == 2
    assert replay.inserted == 0
    assert replay.replayed == 2
    assert tuple(event.kind for event in store.events()) == (
        HermesDeliveryKind.WATCH,
        HermesDeliveryKind.WATCH,
    )
    assert {event.instrument_id for event in store.events()} == {"000660", "005930"}


def test_empty_current_cycle_projects_no_recommendation_once(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    request = _request(())

    # When
    first = project_kr_same_cycle_delivery(store, request)
    replay = project_kr_same_cycle_delivery(store, request)

    # Then
    assert first.inserted == 1
    assert replay.replayed == 1
    assert len(store.events()) == 1
    assert store.events()[0].kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert store.events()[0].status == "censored_no_opportunity"


@pytest.mark.parametrize("fault", ["cycle", "expired"])
def test_invalid_cycle_source_blocks_before_delivery_store_creation(tmp_path: Path, fault: str) -> None:
    # Given
    database = tmp_path / "delivery.sqlite3"
    opportunity = _cycle_opportunity()
    if fault == "cycle":
        request = KrSameCycleDeliveryRequest(
            collection_cycle_id="different-cycle",
            strategy_version=opportunity.producer_strategy_version,
            occurred_at=PROJECTED_AT,
            opportunities=(opportunity,),
        )
    else:
        request = _request((opportunity,), occurred_at=opportunity.valid_until)

    # When / Then
    with pytest.raises(InvalidKrSameCycleDeliveryError):
        _ = project_kr_same_cycle_delivery(HermesDeliveryStore(database), request)
    assert not database.exists()


def _request(
    opportunities: tuple[OpportunitySnapshot, ...],
    *,
    occurred_at: dt.datetime = PROJECTED_AT,
) -> KrSameCycleDeliveryRequest:
    return KrSameCycleDeliveryRequest(
        collection_cycle_id=CYCLE_ID,
        strategy_version="kr-theme-manager-v1",
        occurred_at=occurred_at,
        opportunities=opportunities,
    )


def _cycle_opportunity() -> OpportunitySnapshot:
    source = _opportunity()
    cycle_evidence = EvidenceRef(
        namespace="kr/collection_cycle",
        record_id=CYCLE_ID,
        observed_at=source.observed_at,
    )
    evidence = tuple(sorted((*source.evidence_refs, cycle_evidence), key=lambda item: item.canonical_id))
    return OpportunitySnapshot.model_validate(
        {
            **source.model_dump(mode="python"),
            "evidence_refs": evidence,
        }
    )
