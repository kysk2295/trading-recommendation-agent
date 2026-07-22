from __future__ import annotations

import datetime as dt
from dataclasses import replace

from trading_agent.acceptance_evidence import (
    AcceptanceSessionKind,
    acceptance_artifact_sha256,
    require_clean_repository_commit,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_day_acceptance_evidence import (
    UsDayAcceptanceBuildRequest,
    UsDayAcceptanceEvidenceBundle,
    UsDaySessionTerminal,
    write_us_day_acceptance_evidence,
)
from trading_agent.us_day_operating_cli_contract import (
    EvidenceUsDayCommand,
    FinalizeUsDayCommand,
    RunUsDayCommand,
)
from trading_agent.us_day_operating_models import (
    UsDayOperatingRequest,
    UsDayOperatingResult,
    UsDayOperatingTransition,
)
from trading_agent.us_day_operating_projection import project_us_day_no_recommendation
from trading_agent.us_day_session_inspection import UsDaySessionInspection
from trading_agent.us_day_session_terminal import (
    InvalidUsDaySessionTerminalError,
    UsDayCensoredTerminalObservation,
    UsDayTerminalObservation,
    UsDayTerminalPublication,
    UsDayTerminalRefresh,
    build_censored_us_day_session_terminal,
    build_us_day_session_terminal,
    refresh_us_day_session_terminal,
    write_us_day_session_terminal,
)
from trading_agent.us_equity_calendar import regular_session_bounds


def finalize_us_day_terminal(
    command: FinalizeUsDayCommand,
    inspection: UsDaySessionInspection,
) -> UsDaySessionTerminal:
    if inspection.market_is_open:
        raise InvalidUsDaySessionTerminalError
    if command.terminal_input is not None:
        terminal = _refresh_terminal(command, inspection)
    else:
        terminal = _finalize_no_setup(command, inspection)
    write_us_day_session_terminal(command.paths.terminal_output, terminal)
    return terminal


def publish_us_day_run_terminal(
    command: RunUsDayCommand,
    request: UsDayOperatingRequest,
    result: UsDayOperatingResult,
    inspection: UsDaySessionInspection,
) -> UsDaySessionTerminal:
    if command.terminal_output is None:
        raise InvalidUsDaySessionTerminalError
    state = inspection.broker_state
    reconciled = (
        not state.open_orders
        and not state.positions
        and not state.protective_ocos
        and inspection.reconciliation_passed
        and inspection.broker_shadow_ledger_equal
    )
    transitions = list(result.transitions)
    if reconciled:
        for transition in (UsDayOperatingTransition.FLAT, UsDayOperatingTransition.RECONCILED):
            if transition not in transitions:
                transitions.append(transition)
    attested = replace(result, transitions=tuple(transitions), final_broker_state=state)
    terminal = build_us_day_session_terminal(
        UsDayTerminalObservation(
            attested,
            request.evaluated_at,
            max(request.evaluated_at, inspection.observed_at),
            inspection.reconciliation_passed,
            inspection.broker_shadow_ledger_equal,
        ),
        UsDayTerminalPublication(
            command.authority.repository,
            command.source_artifact_paths,
            AcceptanceSessionKind.REAL,
            "real_session",
            HermesDeliveryStore(command.stores.delivery),
        ),
    )
    write_us_day_session_terminal(command.terminal_output, terminal)
    return terminal


def write_us_day_evidence(
    command: EvidenceUsDayCommand,
    generated_at: dt.datetime,
) -> UsDayAcceptanceEvidenceBundle:
    return write_us_day_acceptance_evidence(
        UsDayAcceptanceBuildRequest(
            repository=command.repository,
            terminal_paths=command.terminal_paths,
            generated_at=generated_at,
        )
    )


def _refresh_terminal(
    command: FinalizeUsDayCommand,
    inspection: UsDaySessionInspection,
) -> UsDaySessionTerminal:
    if command.terminal_input is None:
        raise InvalidUsDaySessionTerminalError
    terminal = UsDaySessionTerminal.model_validate_json(command.terminal_input.read_text(encoding="utf-8"))
    publication = UsDayTerminalPublication(
        command.paths.repository,
        tuple(item.path for item in terminal.source_artifacts),
        terminal.session_kind,
        terminal.fixture_label,
        HermesDeliveryStore(command.paths.delivery_store),
    )
    return refresh_us_day_session_terminal(
        terminal,
        UsDayTerminalRefresh(
            inspection.broker_state,
            inspection.observed_at,
            inspection.reconciliation_passed,
            inspection.broker_shadow_ledger_equal,
        ),
        publication,
    )


def _finalize_no_setup(
    command: FinalizeUsDayCommand,
    inspection: UsDaySessionInspection,
) -> UsDaySessionTerminal:
    _validate_no_setup(command, inspection)
    if command.session_id is None or command.strategy_version is None:
        raise InvalidUsDaySessionTerminalError
    try:
        session_date = dt.date.fromisoformat(command.session_id[-10:])
    except ValueError:
        raise InvalidUsDaySessionTerminalError from None
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise InvalidUsDaySessionTerminalError
    delivery_store = HermesDeliveryStore(command.paths.delivery_store)
    projected = project_us_day_no_recommendation(
        command.session_id,
        command.strategy_version,
        delivery_store,
        inspection.observed_at,
    )
    return build_censored_us_day_session_terminal(
        UsDayCensoredTerminalObservation(
            command.session_id,
            command.strategy_version,
            bounds[0],
            inspection.observed_at,
            inspection.broker_state,
            inspection.reconciliation_passed,
            inspection.broker_shadow_ledger_equal,
            projected.delivery_id,
        ),
        UsDayTerminalPublication(
            command.paths.repository,
            command.source_artifact_paths,
            AcceptanceSessionKind.REAL,
            "real_session",
            delivery_store,
        ),
    )


def _validate_no_setup(command: FinalizeUsDayCommand, inspection: UsDaySessionInspection) -> None:
    state = inspection.broker_state
    if (
        command.session_id is None
        or command.strategy_version is None
        or not command.source_artifact_paths
        or state.open_orders
        or state.positions
        or state.protective_ocos
        or not inspection.reconciliation_passed
        or not inspection.broker_shadow_ledger_equal
    ):
        raise InvalidUsDaySessionTerminalError
    _ = require_clean_repository_commit(command.paths.repository)
    for path in command.source_artifact_paths:
        _ = acceptance_artifact_sha256(command.paths.repository, path)
