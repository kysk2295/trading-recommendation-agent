from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Final, assert_never, override

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_backoff import AlpacaSipDynamicBackoffConfig
from trading_agent.alpaca_sip_dynamic_plan_store import AlpacaSipDynamicPlanStore
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicTerminalEvidence,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_supervisor import (
    AlpacaSipDynamicReconnectRunReport,
    AlpacaSipDynamicReconnectRunStatus,
    run_alpaca_sip_dynamic_reconnect_supervisor,
)
from trading_agent.alpaca_sip_dynamic_subscription import roll_alpaca_sip_dynamic_subscription_plan
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_quote_actionability_manifest import AlpacaSipQuoteActionabilityManifest
from trading_agent.alpaca_sip_quote_actionability_projection import (
    AlpacaSipQuoteActionabilityProjectionResult,
    project_alpaca_sip_quote_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.alpaca_sip_trade_stream import AlpacaSipTradeStreamConnector
from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot
from trading_agent.intraday_feature_reobservation import reobserve_ready_intraday_feature
from trading_agent.kis_live import regular_session_is_open
from trading_agent.us_quote_actionability_rules import base_is_current
from trading_agent.us_runtime_policy_scope import completed_regular_minute
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

_MAX_POLICY_STATE_AGE: Final = dt.timedelta(seconds=90)


class AlpacaSipLiveActionabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP live actionability is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipLiveActionabilityStores:
    plan: AlpacaSipDynamicPlanStore
    policy: SubscriptionPolicyStateStore
    receipt: AlpacaSipDynamicReceiptStore
    output: AlpacaSipQuoteActionabilityStore


@dataclass(frozen=True, slots=True)
class AlpacaSipLiveActionabilityConfig:
    max_attempts: int
    backoff: AlpacaSipDynamicBackoffConfig
    max_data_frames: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= 3
            or type(self.backoff) is not AlpacaSipDynamicBackoffConfig
            or type(self.max_data_frames) is not int
            or not 1 <= self.max_data_frames <= 10
            or type(self.timeout_seconds) is not float
            or not 0 < self.timeout_seconds <= 10
        ):
            raise AlpacaSipLiveActionabilityError


@dataclass(frozen=True, slots=True)
class AlpacaSipLiveActionabilityRequest:
    credentials: AlpacaCredentials
    manifest: AlpacaSipQuoteActionabilityManifest
    stores: AlpacaSipLiveActionabilityStores
    config: AlpacaSipLiveActionabilityConfig


@dataclass(frozen=True, slots=True)
class AlpacaSipLiveActionabilityDependencies:
    connector: AlpacaSipTradeStreamConnector
    clock: Callable[[], dt.datetime]
    epoch_factory: Callable[[], str]
    wait: Callable[[Event, float], bool]


@dataclass(frozen=True, slots=True)
class AlpacaSipLiveActionabilityResult:
    connection: AlpacaSipDynamicReconnectRunReport
    terminal: AlpacaSipDynamicTerminalEvidence
    reobserved_snapshot: IntradayFeatureSnapshot
    projection: AlpacaSipQuoteActionabilityProjectionResult


def run_alpaca_sip_live_actionability(
    request: AlpacaSipLiveActionabilityRequest,
    dependencies: AlpacaSipLiveActionabilityDependencies,
) -> AlpacaSipLiveActionabilityResult:
    try:
        if (
            type(request) is not AlpacaSipLiveActionabilityRequest
            or type(dependencies) is not AlpacaSipLiveActionabilityDependencies
        ):
            raise AlpacaSipLiveActionabilityError
        initial_at = dependencies.clock()
        _require_active_plan(request, initial_at)
        report = run_alpaca_sip_dynamic_reconnect_supervisor(
            request.credentials,
            request.manifest.plan,
            request.stores.receipt,
            max_attempts=request.config.max_attempts,
            backoff=request.config.backoff,
            max_data_frames=request.config.max_data_frames,
            timeout_seconds=request.config.timeout_seconds,
            connector=dependencies.connector,
            _clock=dependencies.clock,
            _epoch_factory=dependencies.epoch_factory,
            _wait=dependencies.wait,
        )
        terminal = _require_complete_terminal(request, report)
        snapshot = reobserve_ready_intraday_feature(request.manifest.snapshot, terminal.terminal_at)
        if not regular_session_is_open(terminal.terminal_at) or not base_is_current(
            request.manifest.base_publication,
            scan_started_at=request.manifest.scan_started_at,
            evaluated_at=terminal.terminal_at,
        ):
            raise AlpacaSipLiveActionabilityError
        projection = project_alpaca_sip_quote_actionability(
            request.manifest.base_publication,
            snapshot,
            request.stores.receipt,
            request.manifest.plan,
            request.stores.output,
            scan_started_at=request.manifest.scan_started_at,
        )
        return AlpacaSipLiveActionabilityResult(report, terminal, snapshot, projection)
    except (AlpacaSipDynamicReceiptError, AttributeError, OSError, TypeError, ValueError):
        raise AlpacaSipLiveActionabilityError from None


def _require_active_plan(request: AlpacaSipLiveActionabilityRequest, observed_at: dt.datetime) -> None:
    plan = request.stores.plan.latest()
    state = request.stores.policy.latest()
    if state is None:
        raise AlpacaSipLiveActionabilityError
    stable = roll_alpaca_sip_dynamic_subscription_plan(plan, state)
    if (
        plan is None
        or plan != request.manifest.plan
        or stable != plan
        or not regular_session_is_open(observed_at)
        or not dt.timedelta(0) <= observed_at - state.evaluated_at <= _MAX_POLICY_STATE_AGE
        or completed_regular_minute(observed_at) != completed_regular_minute(request.manifest.snapshot.observed_at)
        or not base_is_current(
            request.manifest.base_publication,
            scan_started_at=request.manifest.scan_started_at,
            evaluated_at=observed_at,
        )
    ):
        raise AlpacaSipLiveActionabilityError


def _require_complete_terminal(
    request: AlpacaSipLiveActionabilityRequest,
    report: AlpacaSipDynamicReconnectRunReport,
) -> AlpacaSipDynamicTerminalEvidence:
    match report.status:
        case AlpacaSipDynamicReconnectRunStatus.BOUNDED_COMPLETE | AlpacaSipDynamicReconnectRunStatus.BLOCKED_COMPLETE:
            pass
        case (
            AlpacaSipDynamicReconnectRunStatus.BLOCKED_BUDGET
            | AlpacaSipDynamicReconnectRunStatus.BLOCKED_NON_RETRYABLE
            | AlpacaSipDynamicReconnectRunStatus.BLOCKED_CLOCK_REGRESSION
            | AlpacaSipDynamicReconnectRunStatus.STOPPED
        ):
            raise AlpacaSipLiveActionabilityError
        case unreachable:
            assert_never(unreachable)
    history = AlpacaSipDynamicTerminalStore(request.stores.receipt.path).load_history(request.manifest.plan)
    if len(history) != 1:
        raise AlpacaSipLiveActionabilityError
    terminal = history[0]
    match terminal.status:
        case AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE:
            return terminal
        case AlpacaSipDynamicTerminalStatus.FAILED:
            raise AlpacaSipLiveActionabilityError
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "AlpacaSipLiveActionabilityConfig",
    "AlpacaSipLiveActionabilityDependencies",
    "AlpacaSipLiveActionabilityError",
    "AlpacaSipLiveActionabilityRequest",
    "AlpacaSipLiveActionabilityResult",
    "AlpacaSipLiveActionabilityStores",
    "run_alpaca_sip_live_actionability",
)
