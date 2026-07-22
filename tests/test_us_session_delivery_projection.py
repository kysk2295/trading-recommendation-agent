from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from run_hermes_delivery import main
from tests.test_contract_outbox import OBSERVED_AT, _opportunity, _publication
from trading_agent.contract_outbox import (
    append_opportunity_snapshot,
    append_trade_signal_publication,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionSources,
    InvalidHermesProjectionSourceError,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.signal_contract_models import OpportunitySnapshot, TradeSignalEnvelope
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_session_delivery_projection import (
    project_us_session_contract_outboxes,
)


def test_session_projection_deduplicates_watch_and_replies_with_day_signal(
    tmp_path: Path,
) -> None:
    # Given: the same symbol appears in two opportunity cycles and signals only later.
    sources = _sources(tmp_path)
    first = _opportunity()
    second = _later_opportunity(first)
    signal = _later_publication(second)
    assert append_opportunity_snapshot(sources.opportunity_outbox, first) is True
    assert append_opportunity_snapshot(sources.opportunity_outbox, second) is True
    assert append_trade_signal_publication(sources.signal_outbox, tmp_path / "cards", signal) is True
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When: the current NYSE session is projected twice.
    with store.writer() as writer:
        first_result = project_us_session_contract_outboxes(
            sources,
            OBSERVED_AT.date(),
            writer,
        )
        replay = project_us_session_contract_outboxes(
            sources,
            OBSERVED_AT.date(),
            writer,
        )

    # Then: one Opportunity Manager root and one Day Trading reply exist.
    events = store.events()
    assert first_result.inserted == 2
    assert replay.inserted == 0
    assert tuple(event.kind for event in events) == (
        HermesDeliveryKind.WATCH,
        HermesDeliveryKind.WATCH,
    )
    assert tuple(event.agent_family for event in events) == (
        "opportunity_manager",
        "day_trading",
    )
    assert events[1].root_delivery_id == events[0].delivery_id
    assert first.opportunity_id in events[0].source_event_id
    assert second.opportunity_id not in events[0].source_event_id


def test_session_projection_rejects_signal_without_session_opportunity(
    tmp_path: Path,
) -> None:
    # Given: a signal outbox refers to an opportunity absent from the session source.
    sources = _sources(tmp_path)
    publication = _publication(signal_id="orphan-signal")
    assert append_trade_signal_publication(
        sources.signal_outbox,
        tmp_path / "cards",
        publication,
    ) is True
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    # When / Then: projection fails before creating a rootless trading opinion.
    with store.writer() as writer, pytest.raises(InvalidHermesProjectionSourceError):
        _ = project_us_session_contract_outboxes(
            sources,
            OBSERVED_AT.date(),
            writer,
        )
    assert store.events() == ()


def test_project_session_cli_replays_without_duplicate_delivery(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one session has repeated opportunity cycles and one later signal.
    sources = _sources(tmp_path)
    first = _opportunity()
    second = _later_opportunity(first)
    assert append_opportunity_snapshot(sources.opportunity_outbox, first) is True
    assert append_opportunity_snapshot(sources.opportunity_outbox, second) is True
    assert append_trade_signal_publication(
        sources.signal_outbox,
        tmp_path / "cards",
        _later_publication(second),
    ) is True
    database = tmp_path / "delivery.sqlite3"
    arguments = (
        "project-session",
        "--database",
        str(database),
        "--opportunities",
        str(sources.opportunity_outbox),
        "--signals",
        str(sources.signal_outbox),
        "--session-date",
        OBSERVED_AT.date().isoformat(),
    )

    # When: the CLI projects and then replays the same session.
    first_exit = main(arguments)
    first_output = json.loads(capsys.readouterr().out)
    replay_exit = main(arguments)
    replay_output = json.loads(capsys.readouterr().out)

    # Then: only the first run inserts the two delivery identities.
    assert (first_exit, replay_exit) == (0, 0)
    assert first_output == {
        "examined": 2,
        "inserted": 2,
        "result": "projected_session",
    }
    assert replay_output == {
        "examined": 2,
        "inserted": 0,
        "result": "projected_session",
    }


def _sources(root: Path) -> HermesProjectionSources:
    return HermesProjectionSources(
        opportunity_outbox=root / "opportunities.v1.jsonl",
        signal_outbox=root / "trade-signals.v1.jsonl",
    )


def _later_opportunity(first: OpportunitySnapshot) -> OpportunitySnapshot:
    observed_at = first.observed_at + dt.timedelta(minutes=1)
    return OpportunitySnapshot.model_validate(
        {
            **first.model_dump(mode="python"),
            "opportunity_id": "us-opportunity-20260715T140100000000Z-efgh5678",
            "observed_at": observed_at,
            "valid_until": observed_at + dt.timedelta(minutes=1),
            "evidence_refs": tuple(
                item.model_copy(update={"observed_at": observed_at})
                for item in first.evidence_refs
            ),
            "source_coverage": tuple(
                item.model_copy(update={"observed_at": observed_at})
                for item in first.source_coverage
            ),
        }
    )


def _later_publication(opportunity: OpportunitySnapshot) -> TradeSignalPublication:
    base = _publication(signal_id="later-signal")
    observed_at = opportunity.observed_at + dt.timedelta(seconds=5)
    signal = TradeSignalEnvelope.model_validate(
        {
            **base.signal.model_dump(mode="python"),
            "observed_at": observed_at,
            "valid_until": observed_at + dt.timedelta(minutes=1),
            "opportunity_id": opportunity.opportunity_id,
        }
    )
    return TradeSignalPublication(
        published_at=observed_at + dt.timedelta(seconds=5),
        signal=signal,
    )
