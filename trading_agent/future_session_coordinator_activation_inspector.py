from __future__ import annotations

import json
import os
from collections.abc import Callable

from pydantic import ValidationError

from trading_agent.future_session_coordinator_inspectors import (
    CoordinatorInspectionError,
    PreparedSchedule,
)
from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorBlockReason,
    KrFutureSessionActivationReceipt,
    UsFutureSessionActivationReceipt,
)
from trading_agent.future_session_plan_models import FutureSessionMarket
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)
from trading_agent.future_session_us_activation_verifier import (
    PRIVATE_FILE_MODE,
    read_private_file,
)


def inspect_activation(
    market: FutureSessionMarket,
    prepared: PreparedSchedule,
    label_status_reader: Callable[[str], bool],
) -> bool:
    installed = tuple(os.path.lexists(path) for path in prepared.installed_plists)
    receipt_exists = os.path.lexists(prepared.receipt_path)
    if not any(installed) and not receipt_exists:
        return False
    if not all(installed) or not receipt_exists:
        raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.PARTIAL_INSTALLED_SET)
    _inspect_receipt(market, prepared)
    for source, destination in zip(
        prepared.source_plists,
        prepared.installed_plists,
        strict=True,
    ):
        try:
            if read_private_file(source, PRIVATE_FILE_MODE) != read_private_file(
                destination,
                PRIVATE_FILE_MODE,
            ):
                raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.INSTALLED_PLIST_MISMATCH)
        except FutureSessionActivationError:
            raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.INSTALLED_PLIST_MISMATCH) from None
    if not all(label_status_reader(label) for label in prepared.labels):
        raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.LAUNCHCTL_LABEL_MISMATCH)
    return True


def _inspect_receipt(
    market: FutureSessionMarket,
    prepared: PreparedSchedule,
) -> None:
    try:
        payload = read_private_file(prepared.receipt_path, PRIVATE_FILE_MODE)
        match market:
            case FutureSessionMarket.US:
                receipt = UsFutureSessionActivationReceipt.model_validate_json(payload)
                valid = (
                    receipt.labels == prepared.labels
                    and receipt.manifest_sha256 == prepared.manifest_sha256
                    and _canonical_json(receipt) == payload
                )
            case FutureSessionMarket.KR:
                receipt_kr = KrFutureSessionActivationReceipt.model_validate_json(payload)
                valid = (
                    receipt_kr.label == prepared.labels[0]
                    and receipt_kr.manifest_sha256 == prepared.manifest_sha256
                    and _canonical_json(receipt_kr) == payload
                )
    except (FutureSessionActivationError, TypeError, ValidationError, ValueError):
        valid = False
    if not valid:
        raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.ACTIVATION_RECEIPT_MISMATCH)


def _canonical_json(
    receipt: UsFutureSessionActivationReceipt | KrFutureSessionActivationReceipt,
) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


__all__ = ("inspect_activation",)
