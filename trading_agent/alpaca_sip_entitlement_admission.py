from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never

from pydantic import ValidationError

from trading_agent.alpaca_sip_entitlement_artifacts import (
    AlpacaSipEntitlementAdmissionArtifact,
    AlpacaSipEntitlementAdmissionError,
    AlpacaSipEntitlementAdmissionReason,
    AlpacaSipEntitlementAdmissionStatus,
    build_alpaca_sip_entitlement_artifact,
)
from trading_agent.alpaca_sip_trade_stream_attempts import (
    AlpacaSipConnectionFailureCode,
    AlpacaSipFailedConnectionAttempt,
    connection_attempt_content_hash,
)
from trading_agent.alpaca_sip_trade_stream_audit import terminal_content_hash
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamSessionEvidence,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore


@dataclass(frozen=True, slots=True)
class AlpacaSipEntitlementAdmissionUnknown:
    reason_code: Literal["transient_or_missing_evidence"] = "transient_or_missing_evidence"


type AlpacaSipEntitlementAdmissionResult = AlpacaSipEntitlementAdmissionArtifact | AlpacaSipEntitlementAdmissionUnknown


def assess_alpaca_sip_entitlement(
    store: AlpacaSipTradeStreamStore,
    config: AlpacaSipTradeStreamConfig,
) -> AlpacaSipEntitlementAdmissionResult:
    try:
        attempts = store.load_connection_attempts(config)
        sessions = store.load_session_history(config)
        latest_attempt = attempts[-1] if attempts else None
        latest_session = sessions[-1] if sessions else None
        if latest_attempt is not None and (
            latest_session is None or latest_attempt.failed_at >= latest_session.terminal_at
        ):
            return _from_attempt(latest_attempt)
        if latest_session is not None:
            return _from_session(latest_session)
        return AlpacaSipEntitlementAdmissionUnknown()
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise AlpacaSipEntitlementAdmissionError from None


def _from_attempt(
    attempt: AlpacaSipFailedConnectionAttempt,
) -> AlpacaSipEntitlementAdmissionResult:
    match attempt.failure_code:
        case AlpacaSipConnectionFailureCode.INSUFFICIENT_SUBSCRIPTION:
            return build_alpaca_sip_entitlement_artifact(
                config=attempt.config,
                assessed_at=attempt.failed_at,
                status=AlpacaSipEntitlementAdmissionStatus.BLOCKED,
                reason=AlpacaSipEntitlementAdmissionReason.INSUFFICIENT_SUBSCRIPTION,
                evidence_sha256=connection_attempt_content_hash(attempt),
            )
        case (
            AlpacaSipConnectionFailureCode.TRANSPORT_FAILED
            | AlpacaSipConnectionFailureCode.HANDSHAKE_FAILED
            | AlpacaSipConnectionFailureCode.ENDPOINT_REJECTED
            | AlpacaSipConnectionFailureCode.AUTHENTICATION_FAILED
            | AlpacaSipConnectionFailureCode.CONNECTION_LIMIT
            | AlpacaSipConnectionFailureCode.PROVIDER_INTERNAL_ERROR
            | AlpacaSipConnectionFailureCode.PROVIDER_REJECTED
            | AlpacaSipConnectionFailureCode.PROTOCOL_INVALID
        ):
            return AlpacaSipEntitlementAdmissionUnknown()
        case unreachable:
            assert_never(unreachable)


def _from_session(
    session: AlpacaSipTradeStreamSessionEvidence,
) -> AlpacaSipEntitlementAdmissionResult:
    match session.status:
        case AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE:
            record = AlpacaSipStreamTerminalRecord(
                session.connection_epoch,
                session.config,
                session.authorized_at,
                session.subscribed_at,
                session.terminal_at,
                session.status,
            )
            return build_alpaca_sip_entitlement_artifact(
                config=session.config,
                assessed_at=session.terminal_at,
                status=AlpacaSipEntitlementAdmissionStatus.READY,
                reason=AlpacaSipEntitlementAdmissionReason.BOUNDED_COMPLETE,
                evidence_sha256=terminal_content_hash(record, len(session.receipt_ids)),
            )
        case AlpacaSipStreamTerminalStatus.FAILED:
            return AlpacaSipEntitlementAdmissionUnknown()
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "AlpacaSipEntitlementAdmissionResult",
    "AlpacaSipEntitlementAdmissionUnknown",
    "assess_alpaca_sip_entitlement",
)
