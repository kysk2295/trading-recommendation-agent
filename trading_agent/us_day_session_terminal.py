from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never, override

from trading_agent.acceptance_evidence import (
    AcceptanceArtifactEvidence,
    AcceptanceSessionKind,
    InvalidAcceptanceEvidenceError,
    acceptance_artifact_sha256,
    require_clean_repository_commit,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_day_acceptance_models import UsDaySessionTerminal, UsDayTerminalStatus
from trading_agent.us_day_operating_models import (
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)


class InvalidUsDaySessionTerminalError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day session terminal cannot be attested"


@dataclass(frozen=True, slots=True)
class UsDayTerminalObservation:
    result: UsDayOperatingResult
    observed_from: dt.datetime
    observed_through: dt.datetime
    reconciliation_passed: bool
    broker_shadow_ledger_equal: bool


@dataclass(frozen=True, slots=True)
class UsDayTerminalPublication:
    repository: Path
    source_artifact_paths: tuple[Path, ...]
    session_kind: AcceptanceSessionKind
    fixture_label: str
    delivery_store: HermesDeliveryStore


@dataclass(frozen=True, slots=True)
class UsDayCensoredTerminalObservation:
    session_id: str
    strategy_version: str
    observed_from: dt.datetime
    observed_through: dt.datetime
    broker_state: PaperBrokerState
    reconciliation_passed: bool
    broker_shadow_ledger_equal: bool
    outcome_delivery_id: str


@dataclass(frozen=True, slots=True)
class UsDayTerminalRefresh:
    broker_state: PaperBrokerState
    observed_through: dt.datetime
    reconciliation_passed: bool
    broker_shadow_ledger_equal: bool


def build_us_day_session_terminal(
    observation: UsDayTerminalObservation,
    publication: UsDayTerminalPublication,
) -> UsDaySessionTerminal:
    state = observation.result.final_broker_state
    if state is None or not publication.source_artifact_paths:
        raise InvalidUsDaySessionTerminalError
    try:
        commit_sha = require_clean_repository_commit(publication.repository)
        sources = tuple(
            AcceptanceArtifactEvidence(
                path=path,
                sha256=acceptance_artifact_sha256(publication.repository, path),
            )
            for path in publication.source_artifact_paths
        )
    except InvalidAcceptanceEvidenceError:
        raise InvalidUsDaySessionTerminalError from None
    match observation.result.status:
        case UsDayOperatingStatus.COMPLETED:
            terminal_status = UsDayTerminalStatus.COMPLETED
        case UsDayOperatingStatus.BLOCKED:
            terminal_status = UsDayTerminalStatus.BLOCKED
        case UsDayOperatingStatus.INCIDENT:
            terminal_status = UsDayTerminalStatus.INCIDENT
        case unreachable:
            assert_never(unreachable)
    acknowledged = any(
        item.delivery_id == observation.result.outcome_delivery_id
        for item in publication.delivery_store.acknowledgements()
    )
    try:
        return UsDaySessionTerminal(
            commit_sha=commit_sha,
            session_id=observation.result.session_id,
            strategy_version=observation.result.strategy_version,
            session_kind=publication.session_kind,
            fixture_label=publication.fixture_label,
            status=terminal_status,
            reasons=observation.result.reasons,
            observed_from=observation.observed_from,
            observed_through=observation.observed_through,
            transitions=observation.result.transitions,
            open_order_count=len(state.open_orders),
            position_count=len(state.positions),
            protective_oco_count=len(state.protective_ocos),
            reconciliation_passed=observation.reconciliation_passed,
            broker_shadow_ledger_equal=observation.broker_shadow_ledger_equal,
            outcome_delivery_id=observation.result.outcome_delivery_id,
            hermes_acknowledged=acknowledged,
            source_artifacts=sources,
        )
    except ValueError:
        raise InvalidUsDaySessionTerminalError from None


def write_us_day_session_terminal(destination: Path, terminal: UsDaySessionTerminal) -> None:
    write_private_stable_report(destination, terminal.model_dump_json(indent=2) + "\n")


def build_censored_us_day_session_terminal(
    observation: UsDayCensoredTerminalObservation,
    publication: UsDayTerminalPublication,
) -> UsDaySessionTerminal:
    state = observation.broker_state
    if (
        state.open_orders
        or state.positions
        or state.protective_ocos
        or not observation.reconciliation_passed
        or not observation.broker_shadow_ledger_equal
        or not publication.source_artifact_paths
    ):
        raise InvalidUsDaySessionTerminalError
    try:
        commit_sha = require_clean_repository_commit(publication.repository)
        sources = tuple(
            AcceptanceArtifactEvidence(
                path=path,
                sha256=acceptance_artifact_sha256(publication.repository, path),
            )
            for path in publication.source_artifact_paths
        )
        acknowledged = any(
            item.delivery_id == observation.outcome_delivery_id
            for item in publication.delivery_store.acknowledgements()
        )
        return UsDaySessionTerminal(
            commit_sha=commit_sha,
            session_id=observation.session_id,
            strategy_version=observation.strategy_version,
            session_kind=publication.session_kind,
            fixture_label=publication.fixture_label,
            status=UsDayTerminalStatus.CENSORED,
            reasons=("censored_no_setup",),
            observed_from=observation.observed_from,
            observed_through=observation.observed_through,
            transitions=(
                UsDayOperatingTransition.FLAT,
                UsDayOperatingTransition.RECONCILED,
                UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
            ),
            open_order_count=0,
            position_count=0,
            protective_oco_count=0,
            reconciliation_passed=True,
            broker_shadow_ledger_equal=True,
            outcome_delivery_id=observation.outcome_delivery_id,
            hermes_acknowledged=acknowledged,
            source_artifacts=sources,
        )
    except (InvalidAcceptanceEvidenceError, ValueError):
        raise InvalidUsDaySessionTerminalError from None


def refresh_us_day_session_terminal(
    terminal: UsDaySessionTerminal,
    refresh: UsDayTerminalRefresh,
    publication: UsDayTerminalPublication,
) -> UsDaySessionTerminal:
    try:
        commit_sha = require_clean_repository_commit(publication.repository)
        if commit_sha != terminal.commit_sha or tuple(item.path for item in terminal.source_artifacts) != (
            publication.source_artifact_paths
        ):
            raise InvalidUsDaySessionTerminalError
        for source in terminal.source_artifacts:
            if acceptance_artifact_sha256(publication.repository, source.path) != source.sha256:
                raise InvalidUsDaySessionTerminalError
        state = refresh.broker_state
        reconciled = (
            not state.open_orders
            and not state.positions
            and not state.protective_ocos
            and refresh.reconciliation_passed
            and refresh.broker_shadow_ledger_equal
        )
        acknowledged = any(
            item.delivery_id == terminal.outcome_delivery_id
            for item in publication.delivery_store.acknowledgements()
        )
        status = terminal.status if reconciled else UsDayTerminalStatus.INCIDENT
        reasons = terminal.reasons if reconciled else ("final_reconciliation_failed",)
        transitions = list(terminal.transitions)
        if reconciled:
            for transition in (UsDayOperatingTransition.FLAT, UsDayOperatingTransition.RECONCILED):
                if transition not in transitions:
                    transitions.append(transition)
        return UsDaySessionTerminal.model_validate(
            terminal.model_copy(
                update={
                    "status": status,
                    "reasons": reasons,
                    "transitions": tuple(transitions),
                    "observed_through": refresh.observed_through,
                    "open_order_count": len(state.open_orders),
                    "position_count": len(state.positions),
                    "protective_oco_count": len(state.protective_ocos),
                    "reconciliation_passed": refresh.reconciliation_passed,
                    "broker_shadow_ledger_equal": refresh.broker_shadow_ledger_equal,
                    "hermes_acknowledged": acknowledged,
                }
            ).model_dump(mode="python")
        )
    except (InvalidAcceptanceEvidenceError, ValueError):
        raise InvalidUsDaySessionTerminalError from None
