from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import final

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.issuer_announcement_client import IssuerAnnouncementTransportError
from trading_agent.issuer_announcement_models import (
    IssuerAnnouncementContractError,
    IssuerAnnouncementEvent,
    IssuerAnnouncementFailure,
    IssuerAnnouncementOnboarding,
    IssuerAnnouncementRawReceipt,
    IssuerAnnouncementRequest,
    IssuerAnnouncementRunStatus,
    IssuerAnnouncementTerminal,
)
from trading_agent.issuer_announcement_parser import parse_issuer_announcement_feed
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

IssuerAnnouncementFetcher = Callable[
    [IssuerAnnouncementOnboarding, str],
    IssuerAnnouncementRawReceipt,
]


class IssuerAnnouncementStoreError(ValueError):
    def __str__(self) -> str:
        return "issuer announcement store is invalid"


@dataclass(frozen=True, slots=True)
class IssuerAnnouncementCollectionResult:
    terminal: IssuerAnnouncementTerminal
    events: tuple[IssuerAnnouncementEvent, ...]
    replayed: bool


@final
class IssuerAnnouncementArtifactStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = absolute_private_path(root)

    def preflight(self) -> None:
        try:
            descriptor = open_private_parent(self.root, create=True)
            try:
                require_private_directory(descriptor)
                require_open_directory_path(self.root, descriptor)
            finally:
                os.close(descriptor)
        except (OSError, TypeError, ValueError):
            raise IssuerAnnouncementStoreError from None

    def receipt(self, request_id: str) -> IssuerAnnouncementRawReceipt | None:
        payload = self._optional(self._receipt_path(request_id))
        if payload is None:
            return None
        try:
            receipt = IssuerAnnouncementRawReceipt.model_validate_json(payload)
        except (TypeError, ValidationError, ValueError):
            raise IssuerAnnouncementStoreError from None
        if receipt.request_id != request_id:
            raise IssuerAnnouncementStoreError
        return receipt

    def terminal(self, request_id: str) -> IssuerAnnouncementTerminal | None:
        payload = self._optional(self._terminal_path(request_id))
        if payload is None:
            return None
        try:
            terminal = IssuerAnnouncementTerminal.model_validate_json(payload)
        except (TypeError, ValidationError, ValueError):
            raise IssuerAnnouncementStoreError from None
        if terminal.request_id != request_id:
            raise IssuerAnnouncementStoreError
        return terminal

    def append_receipt(self, receipt: IssuerAnnouncementRawReceipt) -> bool:
        try:
            return publish_private_immutable_text(
                self._receipt_path(receipt.request_id),
                canonical_experiment_ledger_json(receipt),
            )
        except InvalidPrivateImmutableFileError:
            raise IssuerAnnouncementStoreError from None

    def append_terminal(self, terminal: IssuerAnnouncementTerminal) -> bool:
        try:
            return publish_private_immutable_text(
                self._terminal_path(terminal.request_id),
                canonical_experiment_ledger_json(terminal),
            )
        except InvalidPrivateImmutableFileError:
            raise IssuerAnnouncementStoreError from None

    def _optional(self, path: Path) -> str | None:
        try:
            _ = path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise IssuerAnnouncementStoreError from None
        try:
            return read_private_text(path)
        except InvalidPrivateImmutableFileError:
            raise IssuerAnnouncementStoreError from None

    def _receipt_path(self, request_id: str) -> Path:
        return self.root / f"{request_id}.receipt.json"

    def _terminal_path(self, request_id: str) -> Path:
        return self.root / f"{request_id}.terminal.json"


def collect_issuer_announcements(
    fetcher: IssuerAnnouncementFetcher,
    store: IssuerAnnouncementArtifactStore,
    request: IssuerAnnouncementRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> IssuerAnnouncementCollectionResult:
    store.preflight()
    terminal = store.terminal(request.request_id)
    receipt = store.receipt(request.request_id)
    if terminal is not None:
        events = _require_projection(request, receipt, terminal)
        return IssuerAnnouncementCollectionResult(terminal, events, True)
    if receipt is None:
        try:
            candidate = fetcher(request.onboarding, request.request_id)
        except IssuerAnnouncementTransportError:
            failed = _terminal(
                request,
                None,
                (),
                _clock(),
                IssuerAnnouncementFailure.TRANSPORT,
            )
            _ = store.append_terminal(failed)
            return IssuerAnnouncementCollectionResult(failed, (), False)
        if candidate.request_id != request.request_id:
            raise IssuerAnnouncementStoreError
        _ = store.append_receipt(candidate)
        receipt = store.receipt(request.request_id)
        if receipt is None:
            raise IssuerAnnouncementStoreError
    terminal, events = _project(request, receipt, _clock())
    _ = store.append_terminal(terminal)
    persisted = store.terminal(request.request_id)
    if persisted != terminal:
        raise IssuerAnnouncementStoreError
    return IssuerAnnouncementCollectionResult(terminal, events, False)


def _require_projection(
    request: IssuerAnnouncementRequest,
    receipt: IssuerAnnouncementRawReceipt | None,
    terminal: IssuerAnnouncementTerminal,
) -> tuple[IssuerAnnouncementEvent, ...]:
    if terminal.failure_code is IssuerAnnouncementFailure.TRANSPORT:
        if receipt is not None:
            raise IssuerAnnouncementStoreError
        expected = _terminal(
            request,
            None,
            (),
            terminal.completed_at,
            IssuerAnnouncementFailure.TRANSPORT,
        )
        if terminal != expected:
            raise IssuerAnnouncementStoreError
        return ()
    if receipt is None:
        raise IssuerAnnouncementStoreError
    expected, events = _project(request, receipt, terminal.completed_at)
    if terminal != expected:
        raise IssuerAnnouncementStoreError
    return events


def _project(
    request: IssuerAnnouncementRequest,
    receipt: IssuerAnnouncementRawReceipt,
    completed_at: dt.datetime,
) -> tuple[IssuerAnnouncementTerminal, tuple[IssuerAnnouncementEvent, ...]]:
    completed = max(completed_at, receipt.received_at)
    if receipt.status_code != 200:
        return (
            _terminal(
                request,
                receipt,
                (),
                completed,
                IssuerAnnouncementFailure.HTTP_STATUS,
            ),
            (),
        )
    try:
        events = parse_issuer_announcement_feed(request.onboarding, receipt)
    except IssuerAnnouncementContractError:
        return (
            _terminal(
                request,
                receipt,
                (),
                completed,
                IssuerAnnouncementFailure.RESPONSE_STRUCTURE,
            ),
            (),
        )
    return _terminal(request, receipt, events, completed, None), events


def _terminal(
    request: IssuerAnnouncementRequest,
    receipt: IssuerAnnouncementRawReceipt | None,
    events: tuple[IssuerAnnouncementEvent, ...],
    completed_at: dt.datetime,
    failure: IssuerAnnouncementFailure | None,
) -> IssuerAnnouncementTerminal:
    return IssuerAnnouncementTerminal(
        request_id=request.request_id,
        completed_at=completed_at,
        status=(
            IssuerAnnouncementRunStatus.SUCCESS
            if failure is None
            else IssuerAnnouncementRunStatus.FAILED
        ),
        failure_code=failure,
        receipt_id=receipt.receipt_id if receipt is not None else None,
        announcement_count=len(events),
        event_ids=tuple(event.event_id for event in events),
        latest_published_at=max(
            (event.published_at for event in events),
            default=None,
        ),
    )


__all__ = (
    "IssuerAnnouncementArtifactStore",
    "IssuerAnnouncementCollectionResult",
    "IssuerAnnouncementFetcher",
    "IssuerAnnouncementStoreError",
    "collect_issuer_announcements",
)
