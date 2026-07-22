from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_contract_outbox import OBSERVED_AT, _opportunity, _publication
from trading_agent.contract_outbox import append_opportunity_snapshot, append_trade_signal_publication
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionSources,
    project_contract_outboxes,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore


def test_contract_projection_preserves_watch_to_signal_reply_lineage(tmp_path: Path) -> None:
    # Given
    sources = HermesProjectionSources(
        opportunity_outbox=tmp_path / "opportunities.v1.jsonl",
        signal_outbox=tmp_path / "trade-signals.v1.jsonl",
    )
    opportunity = _opportunity()
    publication = _publication(signal_id="signal-1")
    assert append_opportunity_snapshot(sources.opportunity_outbox, opportunity) is True
    assert append_trade_signal_publication(sources.signal_outbox, tmp_path / "cards", publication) is True
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When
    with store.writer() as writer:
        first = project_contract_outboxes(sources, writer)
        replay = project_contract_outboxes(sources, writer)
        root_claim = writer.claim_next(worker_id="worker-a", now=OBSERVED_AT, lease_seconds=30)
        assert root_claim is not None
        _ = writer.acknowledge(root_claim, platform_message_id="telegram-100", acknowledged_at=OBSERVED_AT)
        reply_claim = writer.claim_next(
            worker_id="worker-a",
            now=OBSERVED_AT + dt.timedelta(seconds=5),
            lease_seconds=30,
        )

    # Then
    assert first.inserted == 2
    assert replay.inserted == 0
    assert reply_claim is not None
    assert reply_claim.event.agent_family == "day_trading"
    assert reply_claim.lineage.root_delivery_id == root_claim.event.delivery_id
    assert reply_claim.lineage.root_platform_message_id == "telegram-100"


def test_projection_covers_terminal_research_and_summary_delivery_kinds(tmp_path: Path) -> None:
    # Given
    kinds = (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.INVALIDATION,
        HermesDeliveryKind.EXIT,
        HermesDeliveryKind.INCIDENT,
        HermesDeliveryKind.NO_RECOMMENDATION,
        HermesDeliveryKind.RESEARCH,
        HermesDeliveryKind.DAILY_SUMMARY,
    )
    records = tuple(_outcome(kind, index) for index, kind in enumerate(kinds, start=1))
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When
    with store.writer() as writer:
        result = project_outcomes(records, writer)

    # Then
    assert result.inserted == len(kinds)
    assert tuple(event.kind for event in store.events()) == kinds


def _outcome(kind: HermesDeliveryKind, index: int) -> HermesProjectionRecord:
    return HermesProjectionRecord(
        source_event_id=f"outcome-{index}",
        root_source_event_id=None,
        kind=kind,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id="intraday_momentum",
        strategy_version="orb-v1",
        instrument_id="ACME",
        occurred_at=OBSERVED_AT + dt.timedelta(seconds=index),
        status=kind.value,
        evidence_refs=(f"terminal:event-{index}",),
        rendered_text=f"{kind.value} outcome",
        payload_sha256=f"{index:x}" * 64,
    )
