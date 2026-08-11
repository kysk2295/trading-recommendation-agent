from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, assert_never

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials, load_alpaca_paper_credentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_operating_session import open_paper_operating_session
from trading_agent.paper_operating_session_models import PaperOperatingSession, PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import ApprovedPaperOrderGateDecision, BlockedPaperOrderGateDecision
from trading_agent.paper_runtime import PaperRuntimeReadiness


@dataclass(frozen=True, slots=True)
class UsDaySessionInspection:
    broker_state: PaperBrokerState
    observed_at: dt.datetime
    market_is_open: bool
    reconciliation_passed: bool
    broker_shadow_ledger_equal: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UsDayPreflightInspection:
    session: UsDaySessionInspection
    admission_approved: bool
    reasons: tuple[str, ...]


class UsDayReadOnlyOperations(Protocol):
    def preflight(self, execution_store: Path, request: PaperOrderAdmissionRequest) -> UsDayPreflightInspection: ...

    def recover(self, execution_store: Path) -> UsDaySessionInspection: ...


type PaperCredentialsLoader = Callable[[], AlpacaPaperCredentials]
type PaperSessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]


@dataclass(frozen=True, slots=True)
class DefaultUsDayReadOnlyOperations:
    credentials_loader: PaperCredentialsLoader = load_alpaca_paper_credentials
    session_opener: PaperSessionOpener = open_paper_operating_session

    def preflight(self, execution_store: Path, request: PaperOrderAdmissionRequest) -> UsDayPreflightInspection:
        with self.session_opener(self.credentials_loader(), ExecutionStore(execution_store)) as session:
            readiness = session.readiness()
            decision = session.evaluate_order(request)
        inspection = _inspection(readiness)
        match decision:
            case ApprovedPaperOrderGateDecision():
                admission_approved = True
                decision_reasons: tuple[str, ...] = ()
            case BlockedPaperOrderGateDecision(reasons=reasons):
                admission_approved = False
                decision_reasons = reasons
            case unreachable:
                assert_never(unreachable)
        reasons = tuple(sorted(set((*inspection.reasons, *decision_reasons))))
        return UsDayPreflightInspection(inspection, admission_approved and not reasons, reasons)

    def recover(self, execution_store: Path) -> UsDaySessionInspection:
        with self.session_opener(self.credentials_loader(), ExecutionStore(execution_store)) as session:
            readiness = session.readiness()
        return _inspection(readiness)


def _inspection(readiness: PaperRuntimeReadiness) -> UsDaySessionInspection:
    observed_at = max(readiness.broker_state.account.observed_at, readiness.market_clock.observed_at)
    reconciliation_passed = readiness.reconciliation.ready
    return UsDaySessionInspection(
        readiness.broker_state,
        observed_at,
        readiness.market_clock.is_open,
        reconciliation_passed,
        reconciliation_passed,
        readiness.reasons,
    )
