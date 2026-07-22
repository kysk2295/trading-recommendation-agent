from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_contract_outbox import OBSERVED_AT, _opportunity
from tests.test_us_session_delivery_projection import _sources
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.hermes_delivery_models import HermesDeliveryFailure
from trading_agent.hermes_delivery_projection import HermesProjectionSources
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_session_delivery_projection import project_us_session_contract_outboxes
from trading_agent.us_session_delivery_reconciliation import (
    UsSessionDeliveryReconciliationRequest,
    reconcile_us_session_deliveries,
)


def test_reconciliation_completes_intentionally_suppressed_stale_watch(tmp_path: Path) -> None:
    # Given: one exact session WATCH is terminally suppressed by market eligibility.
    sources = _sources(tmp_path)
    assert append_opportunity_snapshot(sources.opportunity_outbox, _opportunity())
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = project_us_session_contract_outboxes(sources, OBSERVED_AT.date(), writer)
        claim = writer.claim_next(
            worker_id="fixture",
            now=OBSERVED_AT + dt.timedelta(minutes=2),
            lease_seconds=30,
        )
        assert claim is not None
        _ = writer.fail(
            claim,
            HermesDeliveryFailure(
                failed_at=OBSERVED_AT + dt.timedelta(minutes=2),
                reason="market_event_ineligible",
                retry_delay_seconds=0,
                terminal=True,
            ),
        )

    # When: exact session delivery reconciliation reads the terminal transition.
    report = reconcile_us_session_deliveries(_request(sources), store)

    # Then: intentional suppression completes without hiding a hard delivery failure.
    assert report.suppressed_count == 1
    assert report.dead_letter_count == 0
    assert report.pending_count == 0
    assert report.complete is True


def test_reconciliation_keeps_telegram_timeout_as_hard_dead_letter(tmp_path: Path) -> None:
    # Given: one exact session WATCH exhausts delivery on a Telegram timeout.
    sources = _sources(tmp_path)
    assert append_opportunity_snapshot(sources.opportunity_outbox, _opportunity())
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = project_us_session_contract_outboxes(sources, OBSERVED_AT.date(), writer)
        claim = writer.claim_next(
            worker_id="fixture",
            now=OBSERVED_AT + dt.timedelta(minutes=2),
            lease_seconds=30,
        )
        assert claim is not None
        _ = writer.fail(
            claim,
            HermesDeliveryFailure(
                failed_at=OBSERVED_AT + dt.timedelta(minutes=2),
                reason="telegram_timeout",
                retry_delay_seconds=0,
                terminal=True,
            ),
        )

    # When: exact session delivery reconciliation reads the terminal transition.
    report = reconcile_us_session_deliveries(_request(sources), store)

    # Then: external delivery failure remains incomplete and unsuppressed.
    assert report.suppressed_count == 0
    assert report.dead_letter_count == 1
    assert report.pending_count == 0
    assert report.complete is False


def _request(sources: HermesProjectionSources) -> UsSessionDeliveryReconciliationRequest:
    return UsSessionDeliveryReconciliationRequest(
        sources=sources,
        session_date=OBSERVED_AT.date(),
        generated_at=OBSERVED_AT + dt.timedelta(minutes=3),
    )
