from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest

from run_hermes_delivery import main
from tests.test_contract_outbox import OBSERVED_AT, _opportunity
from tests.test_us_session_delivery_projection import (
    _later_opportunity,
    _later_publication,
    _sources,
)
from trading_agent.contract_outbox import (
    append_opportunity_snapshot,
    append_trade_signal_publication,
)
from trading_agent.hermes_delivery_models import (
    HermesDeliveryKind,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_projection import HermesProjectionSources
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_session_delivery_projection import (
    project_us_session_contract_outboxes,
)
from trading_agent.us_session_delivery_reconciliation import (
    InvalidUsSessionDeliveryReconciliationError,
    UsSessionDeliveryReconciliationRequest,
    reconcile_us_session_deliveries,
)


def test_reconciliation_tracks_pending_root_then_completes_exact_reply(
    tmp_path: Path,
) -> None:
    # Given: one session root and its later Day signal are durably projected.
    sources = _session_sources(tmp_path)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        projected = project_us_session_contract_outboxes(
            sources,
            OBSERVED_AT.date(),
            writer,
        )
        root = writer.claim_next(
            worker_id="fixture",
            now=OBSERVED_AT + dt.timedelta(minutes=2),
            lease_seconds=30,
        )
        assert root is not None
        assert writer.acknowledge(
            root,
            platform_message_id="fixture-root",
            acknowledged_at=OBSERVED_AT + dt.timedelta(minutes=2),
        )
    request = _request(sources)

    # When: reconciliation runs before and after the reply acknowledgement.
    pending = reconcile_us_session_deliveries(request, store)
    with store.writer() as writer:
        reply = writer.claim_next(
            worker_id="fixture",
            now=OBSERVED_AT + dt.timedelta(minutes=2, seconds=1),
            lease_seconds=30,
        )
        assert reply is not None
        assert reply.lineage.root_platform_message_id == "fixture-root"
        assert writer.acknowledge(
            reply,
            platform_message_id="fixture-reply",
            acknowledged_at=OBSERVED_AT + dt.timedelta(minutes=2, seconds=1),
        )
    complete = reconcile_us_session_deliveries(request, store)

    # Then: exact source identities move from one pending event to fully reconciled.
    assert projected.examined == 2
    assert pending.expected_count == 2
    assert pending.projected_count == 2
    assert pending.acknowledged_count == 1
    assert pending.pending_count == 1
    assert pending.complete is False
    assert complete.acknowledged_count == 2
    assert complete.pending_count == 0
    assert complete.complete is True
    assert complete.source_projection_sha256 == pending.source_projection_sha256


def test_reconciliation_rejects_same_identity_with_different_payload(
    tmp_path: Path,
) -> None:
    # Given: a durable event copied the expected source identity with altered content.
    sources = _session_sources(tmp_path)
    first = _opportunity()
    source_event_id = f"{first.opportunity_id}:{first.candidates[0].symbol}"
    altered = build_hermes_delivery_event(
        kind=HermesDeliveryKind.WATCH,
        source_event_id=source_event_id,
        market_id="us_equities",
        lane_id=first.strategy_lane.canonical_id,
        occurred_at=first.observed_at,
        payload_sha256="f" * 64,
        rendered_text="altered payload",
        agent_family="opportunity_manager",
        instrument_id=first.candidates[0].symbol,
        strategy_version=first.producer_strategy_version,
        status="watch",
    )
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(altered)

    # When / Then: identity equality cannot hide source-content disagreement.
    with pytest.raises(InvalidUsSessionDeliveryReconciliationError):
        _ = reconcile_us_session_deliveries(_request(sources), store)


def test_reconcile_session_cli_writes_private_aggregate_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: both expected deliveries have durable platform acknowledgements.
    sources = _session_sources(tmp_path)
    database = tmp_path / "delivery.sqlite3"
    store = HermesDeliveryStore(database)
    with store.writer() as writer:
        _ = project_us_session_contract_outboxes(sources, OBSERVED_AT.date(), writer)
        for index in range(2):
            at = OBSERVED_AT + dt.timedelta(minutes=2, seconds=index)
            claim = writer.claim_next(worker_id="fixture", now=at, lease_seconds=30)
            assert claim is not None
            assert writer.acknowledge(
                claim,
                platform_message_id=f"fixture-{index}",
                acknowledged_at=at,
            )
    output = tmp_path / "acceptance" / "delivery_reconciliation.json"

    # When: the real CLI surface reconciles this session.
    exit_code = main(
        (
            "reconcile-session",
            "--database",
            str(database),
            "--opportunities",
            str(sources.opportunity_outbox),
            "--signals",
            str(sources.signal_outbox),
            "--session-date",
            OBSERVED_AT.date().isoformat(),
            "--output",
            str(output),
        ),
        clock=lambda: OBSERVED_AT + dt.timedelta(minutes=3),
    )

    # Then: only redacted counts are printed and the exact report remains private.
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "acknowledged": 2,
        "complete": True,
        "expected": 2,
        "pending": 0,
        "result": "reconciled_session",
        "suppressed": 0,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["complete"] is True
    assert "platform_message_id" not in output.read_text(encoding="utf-8")


def test_reconcile_session_cli_redacts_invalid_delivery_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: valid current-session sources point at a corrupt local delivery store.
    sources = _session_sources(tmp_path)
    database = tmp_path / "delivery.sqlite3"
    database.write_bytes(b"not-a-sqlite-database")

    # When: the operator asks the CLI to reconcile it.
    exit_code = main(
        (
            "reconcile-session",
            "--database",
            str(database),
            "--opportunities",
            str(sources.opportunity_outbox),
            "--signals",
            str(sources.signal_outbox),
            "--session-date",
            OBSERVED_AT.date().isoformat(),
            "--output",
            str(tmp_path / "report.json"),
        ),
        clock=lambda: OBSERVED_AT + dt.timedelta(minutes=3),
    )

    # Then: no traceback or local path crosses the CLI boundary.
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason": "invalid_projection_source",
        "result": "blocked",
    }
    assert not (tmp_path / "report.json").exists()


def _session_sources(root: Path) -> HermesProjectionSources:
    sources = _sources(root)
    first = _opportunity()
    second = _later_opportunity(first)
    assert append_opportunity_snapshot(sources.opportunity_outbox, first)
    assert append_opportunity_snapshot(sources.opportunity_outbox, second)
    assert append_trade_signal_publication(
        sources.signal_outbox,
        root / "cards",
        _later_publication(second),
    )
    return sources


def _request(
    sources: HermesProjectionSources,
) -> UsSessionDeliveryReconciliationRequest:
    return UsSessionDeliveryReconciliationRequest(
        sources=sources,
        session_date=OBSERVED_AT.date(),
        generated_at=OBSERVED_AT + dt.timedelta(minutes=3),
    )
