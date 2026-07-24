from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final, override

from pydantic import BaseModel, ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_client import FredTransportError
from trading_agent.fred_alfred_models import (
    FredAlfredError,
    FredAlfredRequest,
    FredFailure,
    FredRawReceipt,
    FredRunStatus,
)
from trading_agent.fred_alfred_parser import parse_fred_alfred_snapshot
from trading_agent.fred_alfred_snapshot_models import FredAlfredTerminal
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


class FredFetcher(Protocol):
    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt: ...


class FredStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED/ALFRED store is invalid"


@dataclass(frozen=True, slots=True)
class FredCollectionResult:
    terminal: FredAlfredTerminal
    replayed: bool
    fetched: bool


@final
class FredArtifactStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = absolute_private_path(root)

    def preflight(self) -> None:
        try:
            descriptor = open_private_parent(self.root, create=True)
            try:
                require_private_directory(descriptor)
            finally:
                os.close(descriptor)
        except (OSError, TypeError, ValueError):
            raise FredStoreError from None

    def receipt(self, request_id: str) -> FredRawReceipt | None:
        return self._read(
            self.root / f"{request_id}.receipt.json",
            FredRawReceipt,
        )

    def terminal(self, request_id: str) -> FredAlfredTerminal | None:
        return self._read(
            self.root / f"{request_id}.terminal.json",
            FredAlfredTerminal,
        )

    def append_receipt(self, receipt: FredRawReceipt) -> bool:
        return self._append(
            self.root / f"{receipt.request_id}.receipt.json",
            canonical_experiment_ledger_json(receipt),
        )

    def append_terminal(self, terminal: FredAlfredTerminal) -> bool:
        return self._append(
            self.root / f"{terminal.request.request_id}.terminal.json",
            canonical_experiment_ledger_json(terminal),
        )

    def _read[T: BaseModel](self, path: Path, model: type[T]) -> T | None:
        try:
            _ = path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise FredStoreError from None
        try:
            return model.model_validate_json(read_private_text(path))
        except (
            InvalidPrivateImmutableFileError,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise FredStoreError from None

    @staticmethod
    def _append(path: Path, payload: str) -> bool:
        try:
            return publish_private_immutable_text(path, payload)
        except InvalidPrivateImmutableFileError:
            raise FredStoreError from None


def collect_fred_alfred(
    fetcher: FredFetcher,
    store: FredArtifactStore,
    request: FredAlfredRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> FredCollectionResult:
    store.preflight()
    existing = store.terminal(request.request_id)
    if existing is not None:
        _validate_replay(store.receipt(request.request_id), existing, request)
        return FredCollectionResult(existing, True, False)
    receipt = store.receipt(request.request_id)
    fetched = receipt is None
    if receipt is None:
        try:
            receipt = fetcher.fetch(request)
        except FredTransportError:
            terminal = _terminal(request, None, _clock(), FredFailure.TRANSPORT)
            _ = store.append_terminal(terminal)
            return FredCollectionResult(terminal, False, True)
        if receipt.request_id != request.request_id:
            raise FredStoreError
        _ = store.append_receipt(receipt)
    terminal = _project(request, receipt, _clock())
    _ = store.append_terminal(terminal)
    return FredCollectionResult(terminal, False, fetched)


def _project(
    request: FredAlfredRequest,
    receipt: FredRawReceipt,
    completed_at: dt.datetime,
) -> FredAlfredTerminal:
    if receipt.status_code != 200:
        return _terminal(request, receipt, completed_at, FredFailure.HTTP_STATUS)
    try:
        snapshot = parse_fred_alfred_snapshot(request, receipt)
    except FredAlfredError:
        return _terminal(
            request,
            receipt,
            completed_at,
            FredFailure.RESPONSE_STRUCTURE,
        )
    return FredAlfredTerminal(
        request=request,
        completed_at=max(completed_at, receipt.received_at),
        status=FredRunStatus.SUCCESS,
        failure=None,
        receipt_id=receipt.receipt_id,
        snapshot=snapshot,
    )


def _terminal(
    request: FredAlfredRequest,
    receipt: FredRawReceipt | None,
    completed_at: dt.datetime,
    failure: FredFailure,
) -> FredAlfredTerminal:
    return FredAlfredTerminal(
        request=request,
        completed_at=completed_at,
        status=FredRunStatus.FAILED,
        failure=failure,
        receipt_id=None if receipt is None else receipt.receipt_id,
        snapshot=None,
    )


def _validate_replay(
    receipt: FredRawReceipt | None,
    terminal: FredAlfredTerminal,
    request: FredAlfredRequest,
) -> None:
    if terminal.request.request_id != request.request_id:
        raise FredStoreError
    if terminal.failure is FredFailure.TRANSPORT:
        if receipt is not None:
            raise FredStoreError
        return
    if receipt is None or _project(request, receipt, terminal.completed_at) != terminal:
        raise FredStoreError


__all__ = (
    "FredArtifactStore",
    "FredCollectionResult",
    "FredFetcher",
    "FredStoreError",
    "collect_fred_alfred",
)
