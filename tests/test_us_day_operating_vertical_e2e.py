from __future__ import annotations

from pathlib import Path

from tests.us_day_operating_fixtures import (
    NaturalPaperSession,
    OneUseArmConsumer,
    OperatingHarness,
    admission,
    operating_request,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.us_day_operating_models import UsDayOperatingStatus, UsDayOperatingTransition


def test_us_day_vertical_closes_entry_protection_exit_reconciliation_and_delivery(tmp_path: Path) -> None:
    # Given
    order_admission = admission()
    arm = OneUseArmConsumer()
    session = NaturalPaperSession(order_admission)

    # When
    result, delivery = OperatingHarness(tmp_path, session).run(operating_request(order_admission), arm)

    # Then
    assert result.status is UsDayOperatingStatus.COMPLETED
    assert result.transitions == (
        UsDayOperatingTransition.ACTIONABLE,
        UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
        UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
        UsDayOperatingTransition.FLAT,
        UsDayOperatingTransition.RECONCILED,
        UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
    )
    assert result.final_broker_state is not None
    assert result.final_broker_state.open_orders == ()
    assert result.final_broker_state.positions == ()
    assert tuple(event.kind for event in delivery.events()) == (HermesDeliveryKind.ACTIONABLE, HermesDeliveryKind.EXIT)
    assert session.entry_calls == 1
    assert session.protection_calls >= 1
    assert len(arm.calls) == 1
