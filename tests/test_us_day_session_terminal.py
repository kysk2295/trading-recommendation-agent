from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_us_day_acceptance_evidence import _clean_repository, _git
from tests.us_day_operating_fixtures import AT, admission, readiness
from trading_agent.acceptance_evidence import AcceptanceSessionKind
from trading_agent.hermes_delivery_models import HermesDeliveryKind, build_hermes_delivery_event
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_day_acceptance_evidence import UsDayTerminalStatus
from trading_agent.us_day_operating_models import (
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)
from trading_agent.us_day_operating_projection import project_us_day_no_recommendation
from trading_agent.us_day_session_terminal import (
    InvalidUsDaySessionTerminalError,
    UsDayCensoredTerminalObservation,
    UsDayTerminalObservation,
    UsDayTerminalPublication,
    UsDayTerminalRefresh,
    build_censored_us_day_session_terminal,
    build_us_day_session_terminal,
    refresh_us_day_session_terminal,
)


def test_completed_operating_result_builds_commit_source_and_ack_bound_terminal(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    order_admission = admission()
    source_path = Path("outputs/source/current-watch.json")
    source = repository / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("current-source", encoding="utf-8")
    delivery_store = HermesDeliveryStore(repository / "outputs/delivery.sqlite3")
    outcome_delivery_id = _acknowledged_outcome(delivery_store)
    result = _completed_result(order_admission.candidate_intent.strategy_version, outcome_delivery_id)
    observation = UsDayTerminalObservation(
        result=result,
        observed_from=AT,
        observed_through=AT.replace(hour=20),
        reconciliation_passed=True,
        broker_shadow_ledger_equal=True,
    )
    publication = UsDayTerminalPublication(
        repository=repository,
        source_artifact_paths=(source_path,),
        session_kind=AcceptanceSessionKind.REAL,
        fixture_label="real_session",
        delivery_store=delivery_store,
    )

    terminal = build_us_day_session_terminal(observation, publication)

    assert terminal.status is UsDayTerminalStatus.COMPLETED
    assert terminal.commit_sha == _git(repository, "rev-parse", "HEAD")
    assert terminal.final_counts == (0, 0, 0)
    assert terminal.hermes_acknowledged is True
    assert terminal.source_artifacts[0].sha256 == hashlib.sha256(b"current-source").hexdigest()


def test_terminal_rejects_missing_final_broker_state(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    result = replace(
        _completed_result(admission().candidate_intent.strategy_version, "b" * 64),
        final_broker_state=None,
    )
    observation = UsDayTerminalObservation(result, AT, AT.replace(hour=20), True, True)
    publication = UsDayTerminalPublication(
        repository,
        (),
        AcceptanceSessionKind.REAL,
        "real_session",
        HermesDeliveryStore(repository / "outputs/delivery.sqlite3"),
    )

    with pytest.raises(InvalidUsDaySessionTerminalError):
        _ = build_us_day_session_terminal(observation, publication)


def test_flat_no_setup_session_builds_censored_terminal_without_natural_lifecycle(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/no-setup.json")
    source = repository / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("no-setup", encoding="utf-8")
    delivery_store = HermesDeliveryStore(repository / "outputs/delivery.sqlite3")
    projected = project_us_day_no_recommendation("XNYS-2026-07-14", "orb-v1", delivery_store, AT)
    state = readiness(admission(), 3).broker_state
    observation = UsDayCensoredTerminalObservation(
        "XNYS-2026-07-14",
        "orb-v1",
        AT,
        AT.replace(hour=20),
        state,
        True,
        True,
        projected.delivery_id,
    )
    publication = UsDayTerminalPublication(
        repository,
        (source_path,),
        AcceptanceSessionKind.REAL,
        "real_session",
        delivery_store,
    )

    terminal = build_censored_us_day_session_terminal(observation, publication)

    assert terminal.status is UsDayTerminalStatus.CENSORED
    assert terminal.reasons == ("censored_no_setup",)
    assert terminal.has_natural_lifecycle is False
    assert terminal.hermes_acknowledged is False


def test_no_setup_projection_replays_after_observation_time_changes(tmp_path: Path) -> None:
    delivery_store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    first = project_us_day_no_recommendation("XNYS-2026-07-14", "orb-v1", delivery_store, AT)
    replayed = project_us_day_no_recommendation(
        "XNYS-2026-07-14",
        "orb-v1",
        delivery_store,
        AT.replace(minute=AT.minute + 5),
    )

    assert replayed == first
    assert len(delivery_store.events()) == 1
    assert delivery_store.events()[0].occurred_at == AT


def test_finalize_refreshes_delivery_ack_without_consuming_another_arm(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/current-watch.json")
    source = repository / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("current-source", encoding="utf-8")
    delivery_store = HermesDeliveryStore(repository / "outputs/delivery.sqlite3")
    outcome_delivery_id = _acknowledged_outcome(delivery_store)
    result = _completed_result(admission().candidate_intent.strategy_version, outcome_delivery_id)
    publication = UsDayTerminalPublication(
        repository,
        (source_path,),
        AcceptanceSessionKind.REAL,
        "real_session",
        delivery_store,
    )
    terminal = build_us_day_session_terminal(
        UsDayTerminalObservation(result, AT, AT.replace(hour=20), True, True),
        publication,
    )
    stale = terminal.model_copy(update={"hermes_acknowledged": False})
    state = readiness(admission(), 3).broker_state

    refreshed = refresh_us_day_session_terminal(
        stale,
        UsDayTerminalRefresh(state, AT.replace(hour=20, minute=1), True, True),
        publication,
    )

    assert refreshed.hermes_acknowledged is True
    assert refreshed.status is UsDayTerminalStatus.COMPLETED
    assert refreshed.final_counts == (0, 0, 0)


def test_finalize_marks_recovered_incident_flat_and_reconciled(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/incident.json")
    source = repository / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("incident", encoding="utf-8")
    order_admission = admission()
    delivery_store = HermesDeliveryStore(repository / "outputs/delivery.sqlite3")
    incident_result = replace(
        _completed_result(order_admission.candidate_intent.strategy_version, "b" * 64),
        status=UsDayOperatingStatus.INCIDENT,
        transitions=(UsDayOperatingTransition.ACTIONABLE, UsDayOperatingTransition.HERMES_RESULT_PROJECTED),
        reasons=("terminal_timeout",),
        final_broker_state=readiness(order_admission, 2).broker_state,
    )
    publication = UsDayTerminalPublication(
        repository,
        (source_path,),
        AcceptanceSessionKind.REAL,
        "real_session",
        delivery_store,
    )
    terminal = build_us_day_session_terminal(
        UsDayTerminalObservation(incident_result, AT, AT.replace(hour=19), False, False),
        publication,
    )

    refreshed = refresh_us_day_session_terminal(
        terminal,
        UsDayTerminalRefresh(readiness(order_admission, 3).broker_state, AT.replace(hour=20), True, True),
        publication,
    )

    assert refreshed.status is UsDayTerminalStatus.INCIDENT
    assert refreshed.reasons == ("terminal_timeout",)
    assert refreshed.is_finally_reconciled is True


def _completed_result(strategy_version: str, outcome_delivery_id: str) -> UsDayOperatingResult:
    order_admission = admission()
    return UsDayOperatingResult(
        UsDayOperatingStatus.COMPLETED,
        (
            UsDayOperatingTransition.ACTIONABLE,
            UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
            UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
            UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
        ),
        (),
        "XNYS-2026-07-14",
        strategy_version,
        order_admission.candidate_intent.intent_id,
        readiness(order_admission, 3).broker_state,
        "a" * 64,
        outcome_delivery_id,
    )


def _acknowledged_outcome(store: HermesDeliveryStore) -> str:
    event = build_hermes_delivery_event(
        kind=HermesDeliveryKind.EXIT,
        source_event_id="us-day-terminal-fixture",
        market_id="us_equities",
        lane_id="intraday_momentum",
        occurred_at=AT,
        payload_sha256="c" * 64,
        rendered_text="fixture outcome",
    )
    with store.writer() as writer:
        _ = writer.append_event(event)
        claim = writer.claim_next(worker_id="fixture-worker", now=AT, lease_seconds=30)
        assert claim is not None
        _ = writer.acknowledge(claim, platform_message_id="fixture-message", acknowledged_at=AT)
    return event.delivery_id
