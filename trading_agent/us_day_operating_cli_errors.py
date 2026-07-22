from __future__ import annotations

import sqlite3
from typing import Final, override

import httpx2
from pydantic import ValidationError

from trading_agent.acceptance_evidence import InvalidAcceptanceEvidenceError
from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_activities import PaperActivityHistoryIncompleteError
from trading_agent.alpaca_paper_client import PaperOrderReadIncompleteError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperSecretEncodingError,
    AlpacaPaperSecretFileError,
    MissingAlpacaPaperCredentialsError,
)
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamError
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import WriterLeaseUnavailableError
from trading_agent.hermes_arm_request import InvalidHermesArmRequestError
from trading_agent.hermes_delivery_projection import InvalidHermesProjectionSourceError
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
)
from trading_agent.paper_entry_source import InvalidCurrentOrbPaperEntrySourceError
from trading_agent.paper_mutation_recovery import (
    InvalidPaperMutationRecoverySnapshotError,
    PaperMutationRecoveryAccountError,
)
from trading_agent.paper_mutation_store import InvalidPaperMutationTransitionError, PaperMutationConflictError
from trading_agent.paper_mutation_validation import InvalidPaperMutationRecordError
from trading_agent.paper_operating_session_models import (
    PaperMutationRecoveryBarrierError,
    PaperPostMutationReconciliationError,
)
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery import InvalidPaperStreamRecoveryError, PaperStreamRecoveryConflictError
from trading_agent.paper_stream_recovery_runtime import PaperStreamRecoveryIncompleteError
from trading_agent.paper_trade_update_runtime import PaperTradeUpdateRecoveryProbeError
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)
from trading_agent.us_day_acceptance_models import InvalidUsDayAcceptanceEvidenceError
from trading_agent.us_day_operating_cli_contract import InvalidUsDayCliCommandError
from trading_agent.us_day_operating_models import InvalidUsDayOperatingRequestError
from trading_agent.us_day_session_terminal import InvalidUsDaySessionTerminalError


class UninitializedUsDayExecutionStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US Day execution store is not initialized"


US_DAY_OPERATIONAL_ERRORS: Final[tuple[type[BaseException], ...]] = (
    AccountBindingConflictError,
    AlpacaApiError,
    AlpacaPaperSecretEncodingError,
    AlpacaPaperSecretFileError,
    ExecutionSchemaIntegrityError,
    InvalidAcceptanceEvidenceError,
    InvalidCurrentOrbPaperEntrySourceError,
    InvalidHermesArmRequestError,
    InvalidHermesProjectionSourceError,
    InvalidPaperAccountActivityError,
    InvalidPaperMutationRecordError,
    InvalidPaperMutationRecoverySnapshotError,
    InvalidPaperMutationTransitionError,
    InvalidPaperStreamRecoveryError,
    InvalidProtectiveOcoRecoveryError,
    InvalidTradeUpdateRawReceiptError,
    InvalidUsDayAcceptanceEvidenceError,
    InvalidUsDayCliCommandError,
    InvalidUsDayOperatingRequestError,
    InvalidUsDaySessionTerminalError,
    MissingAlpacaPaperCredentialsError,
    OSError,
    PaperAccountActivityConflictError,
    PaperActivityHistoryIncompleteError,
    PaperMutationConflictError,
    PaperMutationRecoveryAccountError,
    PaperMutationRecoveryBarrierError,
    PaperOrderReadIncompleteError,
    PaperOrderStreamError,
    PaperPostMutationReconciliationError,
    PaperRuntimeEpochChangedError,
    PaperStreamRecoveryConflictError,
    PaperStreamRecoveryIncompleteError,
    PaperTradeUpdateRecoveryProbeError,
    ProtectiveOcoRecoveryConflictError,
    TradeUpdateReceiptConflictError,
    UninitializedUsDayExecutionStoreError,
    UnknownTradeUpdateReceiptError,
    UnsupportedExecutionSchemaError,
    ValidationError,
    WriterLeaseUnavailableError,
    httpx2.HTTPError,
    sqlite3.Error,
    UnicodeError,
)


def safe_operational_reason(error: BaseException) -> str:
    if isinstance(error, InvalidHermesArmRequestError):
        return error.reason.value
    if isinstance(error, UninitializedUsDayExecutionStoreError):
        return "uninitialized_execution_store"
    if isinstance(error, InvalidCurrentOrbPaperEntrySourceError):
        return "invalid_current_orb_source"
    if isinstance(error, InvalidUsDaySessionTerminalError):
        return "invalid_session_terminal"
    if isinstance(error, InvalidUsDayAcceptanceEvidenceError):
        return "invalid_acceptance_evidence"
    return type(error).__name__
