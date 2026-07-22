from __future__ import annotations

from typing_extensions import override  # noqa: UP035 - Hermes gateway uses Python 3.11.


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
