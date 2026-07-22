from __future__ import annotations

from typing import override


class HermesDeliveryConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Hermes delivery immutable identity conflicts"


class HermesDeliveryLeaseLostError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Hermes delivery claim lease is no longer active"


class HermesDeliveryWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Hermes delivery writer lease is unavailable"


class InvalidHermesDeliveryStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Hermes delivery store is invalid"


class InvalidHermesDeliveryOperationError(ValueError):
    pass
