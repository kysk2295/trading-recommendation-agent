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
from tests.test_us_session_delivery_reconciliation import _session_sources
from trading_agent.contract_outbox import (
    append_opportunity_snapshot,
    append_trade_signal_publication,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionSources
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_session_delivery_projection import (
    project_us_session_contract_outboxes,
)
from trading_agent.us_session_delivery_reconciliation import (
    UsSessionDeliveryReconciliationRequest,
    reconcile_us_session_deliveries,
)
from trading_agent.us_session_delivery_terminal import (
    InvalidUsSessionDeliveryTerminalError,
    UsSessionDeliveryTerminalRequest,
    project_us_session_delivery_terminal,
)
from trading_agent.us_session_delivery_terminal_artifact import (
    read_us_session_delivery_terminal,
    write_us_session_delivery_terminal,
)

FINALIZED_AT = dt.datetime(2026, 7, 15, 20, 1, tzinfo=dt.UTC)


def test_terminal_rejects_projection_before_regular_session_close(
    tmp_path: Path,
) -> None:
    # Given: a valid opportunity source is observed while the regular session is open.
    sources = _opportunity_only_sources(tmp_path)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    request = UsSessionDeliveryTerminalRequest(
        sources=sources,
        session_date=OBSERVED_AT.date(),
        evaluated_at=OBSERVED_AT,
    )

    # When / Then: no terminal opinion can be created from an incomplete session.
    with pytest.raises(InvalidUsSessionDeliveryTerminalError):
        _ = project_us_session_delivery_terminal(request, store)
    assert store.events() == ()


def test_no_signal_terminal_projects_private_no_recommendation_once(
    tmp_path: Path,
) -> None:
    # Given: repeated opportunity cycles finished without a Day signal.
    sources = _opportunity_only_sources(tmp_path)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    request = UsSessionDeliveryTerminalRequest(
        sources=sources,
        session_date=OBSERVED_AT.date(),
        evaluated_at=FINALIZED_AT,
    )
    artifact_path = tmp_path / "terminal.json"

    # When: terminal projection and its exact replay are published.
    first = project_us_session_delivery_terminal(request, store)
    replay = project_us_session_delivery_terminal(
        request.model_copy(
            update={"evaluated_at": FINALIZED_AT + dt.timedelta(minutes=1)}
        ),
        store,
    )
    write_us_session_delivery_terminal(artifact_path, first.artifact)
    persisted = read_us_session_delivery_terminal(artifact_path)

    # Then: one no-setup result is durable and the source-bound artifact is private.
    assert (first.inserted, replay.inserted) == (1, 0)
    assert first.artifact.event.kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert first.artifact.watch_count == 1
    assert first.artifact.signal_count == 0
    assert persisted == first.artifact
    assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600
    assert len(store.events()) == 1


def test_signal_terminal_is_daily_summary_and_final_reconciliation_scope(
    tmp_path: Path,
) -> None:
    # Given: one deduplicated watch and one later Day signal are projected.
    sources = _session_sources(tmp_path)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = project_us_session_contract_outboxes(
            sources,
            OBSERVED_AT.date(),
            writer,
        )
    terminal = project_us_session_delivery_terminal(
        UsSessionDeliveryTerminalRequest(
            sources=sources,
            session_date=OBSERVED_AT.date(),
            evaluated_at=FINALIZED_AT,
        ),
        store,
    ).artifact

    # When: every root, reply, and terminal delivery receives an acknowledgement.
    with store.writer() as writer:
        for index in range(3):
            at = FINALIZED_AT + dt.timedelta(seconds=index)
            claim = writer.claim_next(worker_id="fixture", now=at, lease_seconds=30)
            assert claim is not None
            assert writer.acknowledge(
                claim,
                platform_message_id=f"fixture-{index}",
                acknowledged_at=at,
            )
    report = reconcile_us_session_deliveries(
        UsSessionDeliveryReconciliationRequest(
            sources=sources,
            session_date=OBSERVED_AT.date(),
            generated_at=FINALIZED_AT + dt.timedelta(seconds=3),
            terminal_artifact=terminal,
        ),
        store,
    )

    # Then: the summary is non-performance evidence and all three identities reconcile.
    assert terminal.event.kind is HermesDeliveryKind.DAILY_SUMMARY
    assert terminal.watch_count == 1
    assert terminal.signal_count == 1
    assert "ACME" in terminal.event.rendered_text
    assert report.expected_count == 3
    assert report.acknowledged_count == 3
    assert report.complete is True


def test_terminal_rejects_late_source_change_after_first_terminal(
    tmp_path: Path,
) -> None:
    # Given: a no-setup terminal is already fixed for the closed session.
    sources = _opportunity_only_sources(tmp_path)
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    request = UsSessionDeliveryTerminalRequest(
        sources=sources,
        session_date=OBSERVED_AT.date(),
        evaluated_at=FINALIZED_AT,
    )
    first = project_us_session_delivery_terminal(request, store)
    second_opportunity = _later_opportunity(_opportunity())
    assert append_trade_signal_publication(
        sources.signal_outbox,
        tmp_path / "cards",
        _later_publication(second_opportunity),
    )

    # When / Then: a changed source cannot create a second terminal for the session.
    with pytest.raises(InvalidUsSessionDeliveryTerminalError):
        _ = project_us_session_delivery_terminal(
            request.model_copy(
                update={"evaluated_at": FINALIZED_AT + dt.timedelta(minutes=2)}
            ),
            store,
        )
    assert store.events() == (first.artifact.event,)


def test_finalize_session_cli_writes_redacted_terminal_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a completed session has opportunity evidence but no eligible signal.
    sources = _opportunity_only_sources(tmp_path)
    database = tmp_path / "delivery.sqlite3"
    output = tmp_path / "terminal.json"

    # When: the real CLI finalizes the session after the published close.
    exit_code = main(
        (
            "finalize-session",
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
        clock=lambda: FINALIZED_AT,
    )

    # Then: only aggregate status crosses stdout and exact evidence stays private.
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "inserted": 1,
        "kind": "no_recommendation",
        "result": "finalized_session",
        "signals": 0,
        "watches": 1,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "platform_message_id" not in output.read_text(encoding="utf-8")


def _opportunity_only_sources(root: Path) -> HermesProjectionSources:
    sources = _sources(root)
    first = _opportunity()
    assert append_opportunity_snapshot(sources.opportunity_outbox, first)
    assert append_opportunity_snapshot(
        sources.opportunity_outbox,
        _later_opportunity(first),
    )
    return sources
