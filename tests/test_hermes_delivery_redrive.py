from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from run_hermes_delivery import main
from trading_agent.hermes_delivery_models import (
    HermesDeliveryFailure,
    HermesDeliveryKind,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore

AT = dt.datetime(2026, 7, 22, 15, 30, tzinfo=dt.UTC)


def test_cli_redrives_timeout_dead_letter_as_new_root_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    database = tmp_path / "delivery.sqlite3"
    store = _dead_lettered_store(database, reason="telegram_timeout")
    transition = store.dead_letters()[0]

    # When
    first_exit = main(_arguments(database, transition.transition_id))
    first_output = json.loads(capsys.readouterr().out)
    second_exit = main(_arguments(database, transition.transition_id))
    second_output = json.loads(capsys.readouterr().out)

    # Then
    assert first_exit == 0
    assert second_exit == 0
    assert first_output == {"inserted": 1, "replayed": 0, "result": "redriven"}
    assert second_output == {"inserted": 0, "replayed": 1, "result": "redriven"}
    events = store.events()
    assert len(events) == 2
    original, redrive = events
    assert redrive.delivery_id == redrive.root_delivery_id
    assert redrive.delivery_id != original.delivery_id
    assert redrive.rendered_text == original.rendered_text
    assert redrive.kind is original.kind
    assert redrive.max_attempts == original.max_attempts
    assert redrive.evidence_refs == tuple(
        sorted((*original.evidence_refs, f"hermes-dead-letter:{transition.transition_id}"))
    )


def test_cli_blocks_redrive_for_terminal_rejection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    database = tmp_path / "delivery.sqlite3"
    store = _dead_lettered_store(database, reason="telegram_rejected")
    transition = store.dead_letters()[0]

    # When
    exit_code = main(_arguments(database, transition.transition_id))
    output = json.loads(capsys.readouterr().out)

    # Then
    assert exit_code == 2
    assert output == {"reason": "invalid_projection_source", "result": "blocked"}
    assert len(store.events()) == 1


def test_cli_blocks_unknown_dead_letter_without_creating_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    database = tmp_path / "delivery.sqlite3"

    # When
    exit_code = main(_arguments(database, "a" * 64))
    output = json.loads(capsys.readouterr().out)

    # Then
    assert exit_code == 2
    assert output == {"reason": "invalid_projection_source", "result": "blocked"}
    assert not database.exists()


def _dead_lettered_store(database: Path, *, reason: str) -> HermesDeliveryStore:
    store = HermesDeliveryStore(database)
    with store.writer() as writer:
        _ = writer.append_event(
            build_hermes_delivery_event(
                kind=HermesDeliveryKind.INCIDENT,
                source_event_id="delivery-redrive-test",
                market_id="kr_equities",
                lane_id=None,
                occurred_at=AT,
                payload_sha256="b" * 64,
                rendered_text="KR recommendation blocked by incomplete source coverage.",
                agent_family="opportunity_manager",
                status="blocked_source_incomplete",
                evidence_refs=("kr-source-run:test",),
                max_attempts=1,
            )
        )
        claim = writer.claim_next(worker_id="fixture", now=AT, lease_seconds=30)
        assert claim is not None
        _ = writer.fail(
            claim,
            HermesDeliveryFailure(
                failed_at=AT + dt.timedelta(seconds=1),
                reason=reason,
                retry_delay_seconds=0,
                terminal=reason != "telegram_timeout",
            ),
        )
    return store


def _arguments(database: Path, transition_id: str) -> tuple[str, ...]:
    return (
        "redrive",
        "--database",
        str(database),
        "--dead-letter-transition-id",
        transition_id,
    )
