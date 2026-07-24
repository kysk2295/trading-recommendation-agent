from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final, override

from pydantic import BaseModel, ValidationError

from trading_agent.arxiv_research_client import ArxivTransportError
from trading_agent.arxiv_research_models import (
    ArxivFailure,
    ArxivRawReceipt,
    ArxivResearchError,
    ArxivResearchRequest,
    ArxivRunStatus,
    ArxivTerminal,
)
from trading_agent.arxiv_research_parser import parse_arxiv_research
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
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


class ArxivFetcher(Protocol):
    def fetch(self, request: ArxivResearchRequest) -> ArxivRawReceipt: ...


class ArxivStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "arXiv research store is invalid"


@dataclass(frozen=True, slots=True)
class ArxivCollectionResult:
    terminal: ArxivTerminal
    replayed: bool
    fetched: bool


@final
class ArxivArtifactStore:
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
            raise ArxivStoreError from None

    def receipt(self, request_id: str) -> ArxivRawReceipt | None:
        return self._read(self.root / f"{request_id}.receipt.json", ArxivRawReceipt)

    def terminal(self, request_id: str) -> ArxivTerminal | None:
        return self._read(self.root / f"{request_id}.terminal.json", ArxivTerminal)

    def append_receipt(self, receipt: ArxivRawReceipt) -> bool:
        return self._append(
            self.root / f"{receipt.request_id}.receipt.json",
            canonical_experiment_ledger_json(receipt),
        )

    def append_terminal(self, terminal: ArxivTerminal) -> bool:
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
            raise ArxivStoreError from None
        try:
            return model.model_validate_json(read_private_text(path))
        except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
            raise ArxivStoreError from None

    @staticmethod
    def _append(path: Path, payload: str) -> bool:
        try:
            return publish_private_immutable_text(path, payload)
        except InvalidPrivateImmutableFileError:
            raise ArxivStoreError from None


def collect_arxiv_research(
    fetcher: ArxivFetcher,
    store: ArxivArtifactStore,
    request: ArxivResearchRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> ArxivCollectionResult:
    store.preflight()
    existing = store.terminal(request.request_id)
    if existing is not None:
        _validate_replay(store.receipt(request.request_id), existing, request)
        return ArxivCollectionResult(existing, True, False)
    receipt = store.receipt(request.request_id)
    fetched = receipt is None
    if receipt is None:
        try:
            receipt = fetcher.fetch(request)
        except ArxivTransportError:
            terminal = _terminal(request, None, _clock(), ArxivFailure.TRANSPORT)
            _ = store.append_terminal(terminal)
            return ArxivCollectionResult(terminal, False, True)
        if receipt.request_id != request.request_id:
            raise ArxivStoreError
        _ = store.append_receipt(receipt)
    terminal = _project(request, receipt, _clock())
    _ = store.append_terminal(terminal)
    return ArxivCollectionResult(terminal, False, fetched)


def _project(
    request: ArxivResearchRequest,
    receipt: ArxivRawReceipt,
    completed_at: dt.datetime,
) -> ArxivTerminal:
    if receipt.status_code != 200:
        return _terminal(request, receipt, completed_at, ArxivFailure.HTTP_STATUS)
    try:
        snapshot = parse_arxiv_research(request, receipt)
    except ArxivResearchError:
        return _terminal(request, receipt, completed_at, ArxivFailure.RESPONSE_STRUCTURE)
    return ArxivTerminal(
        request=request,
        completed_at=max(completed_at, receipt.received_at),
        status=ArxivRunStatus.SUCCESS,
        failure=None,
        receipt_id=receipt.receipt_id,
        snapshot=snapshot,
    )


def _terminal(
    request: ArxivResearchRequest,
    receipt: ArxivRawReceipt | None,
    completed_at: dt.datetime,
    failure: ArxivFailure,
) -> ArxivTerminal:
    return ArxivTerminal(
        request=request,
        completed_at=completed_at,
        status=ArxivRunStatus.FAILED,
        failure=failure,
        receipt_id=None if receipt is None else receipt.receipt_id,
        snapshot=None,
    )


def _validate_replay(
    receipt: ArxivRawReceipt | None,
    terminal: ArxivTerminal,
    request: ArxivResearchRequest,
) -> None:
    if terminal.request.request_id != request.request_id:
        raise ArxivStoreError
    if terminal.failure is ArxivFailure.TRANSPORT:
        if receipt is not None:
            raise ArxivStoreError
        return
    if receipt is None or _project(request, receipt, terminal.completed_at) != terminal:
        raise ArxivStoreError


__all__ = (
    "ArxivArtifactStore",
    "ArxivCollectionResult",
    "ArxivFetcher",
    "ArxivStoreError",
    "collect_arxiv_research",
)
