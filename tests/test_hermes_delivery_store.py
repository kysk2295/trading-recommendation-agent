from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest

from trading_agent.hermes_delivery_models import (
    HermesDeliveryFailure,
    HermesDeliveryKind,
    HermesDeliveryTransitionKind,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_store import (
    HermesDeliveryConflictError,
    HermesDeliveryLeaseLostError,
    HermesDeliveryStore,
    HermesDeliveryWriterLeaseUnavailableError,
)

AT = dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC)


def test_delivery_restarts_from_expired_claim_without_duplicate_identity(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    event = _event("signal-1")
    with store.writer() as writer:
        assert writer.append_event(event).inserted is True
        first = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
    assert first is not None

    # When
    with store.writer() as writer:
        second = writer.claim_next(worker_id="worker-b", now=AT + dt.timedelta(seconds=31), lease_seconds=30)

    # Then
    assert second is not None
    assert first.event.delivery_id == second.event.delivery_id
    assert second.attempt.attempt_number == 2
    assert store.events() == (event,)


def test_delivery_rejects_conflicting_duplicate_content(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    event = _event("signal-1")
    changed = event.model_copy(update={"rendered_text": "changed"})

    # When / Then
    with store.writer() as writer:
        assert writer.append_event(event).inserted is True
        assert writer.append_event(event).inserted is False
        with pytest.raises(HermesDeliveryConflictError):
            _ = writer.append_event(changed)


def test_acknowledgement_after_lease_loss_is_rejected(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1"))
        claim = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
    assert claim is not None

    # When / Then
    with store.writer() as writer, pytest.raises(HermesDeliveryLeaseLostError):
        _ = writer.acknowledge(
            claim,
            platform_message_id="telegram-100",
            acknowledged_at=AT + dt.timedelta(seconds=31),
        )


def test_retry_budget_exhaustion_appends_dead_letter(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1", max_attempts=2))
        first = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
        assert first is not None
        first_failure = writer.fail(
            first,
            HermesDeliveryFailure(
                failed_at=AT + dt.timedelta(seconds=1),
                reason="telegram_timeout",
                retry_delay_seconds=5,
            ),
        )
        second = writer.claim_next(worker_id="worker-b", now=AT + dt.timedelta(seconds=6), lease_seconds=30)
        assert second is not None

        # When
        terminal = writer.fail(
            second,
            HermesDeliveryFailure(
                failed_at=AT + dt.timedelta(seconds=7),
                reason="telegram_rejected",
                retry_delay_seconds=5,
            ),
        )

    # Then
    assert first_failure.kind is HermesDeliveryTransitionKind.RETRY_SCHEDULED
    assert terminal.kind is HermesDeliveryTransitionKind.DEAD_LETTER
    assert store.dead_letters() == (terminal,)
    with store.writer() as writer:
        assert writer.claim_next(worker_id="worker-c", now=AT + dt.timedelta(minutes=1), lease_seconds=30) is None


def test_reply_claim_exposes_acknowledged_root_message_id(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    root = _event("signal-1")
    reply = _event("signal-1-exit", root_delivery_id=root.delivery_id, kind=HermesDeliveryKind.EXIT)
    with store.writer() as writer:
        _ = writer.append_event(root)
        _ = writer.append_event(reply)
        root_claim = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
        assert root_claim is not None
        assert root_claim.lineage.root_platform_message_id is None
        _ = writer.acknowledge(root_claim, platform_message_id="telegram-100", acknowledged_at=AT)

        # When
        reply_claim = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)

    # Then
    assert reply_claim is not None
    assert reply_claim.event.delivery_id == reply.delivery_id
    assert reply_claim.lineage.root_delivery_id == root.delivery_id
    assert reply_claim.lineage.root_platform_message_id == "telegram-100"


def test_store_is_append_only_mode_600_and_single_writer(tmp_path: Path) -> None:
    # Given
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1"))
        first = writer.claim_next(worker_id="worker-a", now=AT, lease_seconds=30)
        assert first is not None
        _ = writer.fail(
            first,
            HermesDeliveryFailure(
                failed_at=AT + dt.timedelta(seconds=1),
                reason="telegram_timeout",
                retry_delay_seconds=1,
            ),
        )
        second = writer.claim_next(worker_id="worker-a", now=AT + dt.timedelta(seconds=2), lease_seconds=30)
        assert second is not None
        _ = writer.acknowledge(second, platform_message_id="telegram-100", acknowledged_at=AT + dt.timedelta(seconds=2))
        with pytest.raises(HermesDeliveryWriterLeaseUnavailableError), HermesDeliveryStore(store.path).writer():
            pass

    # When / Then
    with sqlite3.connect(store.path) as connection:
        tables = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        triggers = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        )
        for table in tables:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(f"DELETE FROM {table}")

    assert tables == {
        "hermes_delivery_acknowledgements",
        "hermes_delivery_attempts",
        "hermes_delivery_events",
        "hermes_delivery_transitions",
    }
    assert len(triggers) == 8
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def _event(
    source_event_id: str,
    *,
    root_delivery_id: str | None = None,
    kind: HermesDeliveryKind = HermesDeliveryKind.ACTIONABLE,
    max_attempts: int = 3,
):
    return build_hermes_delivery_event(
        kind=kind,
        source_event_id=source_event_id,
        market_id="us_equities",
        lane_id="intraday_momentum",
        occurred_at=AT,
        payload_sha256="a" * 64,
        rendered_text=f"delivery for {source_event_id}",
        root_delivery_id=root_delivery_id,
        max_attempts=max_attempts,
    )
