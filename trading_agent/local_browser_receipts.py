from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import assert_never

from trading_agent.local_browser_gateway_wire import (
    BrowserRequest,
    canonical_browser_request,
    canonical_browser_response,
    parse_browser_response,
    request_action,
)
from trading_agent.local_browser_private_fs import InvalidLocalBrowserPrivateFsError
from trading_agent.local_browser_protocol import (
    BrowserCaptureRequest,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserReadRequest,
    BrowserResponse,
    BrowserSearchRequest,
    BrowserStatusRequest,
)
from trading_agent.local_browser_receipt_sqlite import (
    PrivateBrowserReceiptDatabase,
    open_private_browser_receipt_database,
)


class InvalidLocalBrowserReceiptError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str = "browser_receipt_invalid") -> None:
        self.reason = reason
        super().__init__(reason)


class LocalBrowserReceiptConflictError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason = "browser_request_id_conflict"
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class BrowserReceiptMetadata:
    target_id: str | None
    normalized_url: str | None
    observation_sha256: str | None
    screenshot_sha256: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserReceipt:
    request: BrowserRequest
    request_sha256: str
    response: BrowserResponse
    response_json: bytes
    response_sha256: str
    metadata: BrowserReceiptMetadata


def browser_receipt(request: BrowserRequest, response: BrowserResponse, occurred_at: datetime) -> BrowserReceipt:
    request_json = canonical_browser_request(request)
    response_json = canonical_browser_response(response)
    return BrowserReceipt(
        request=request,
        request_sha256=hashlib.sha256(request_json).hexdigest(),
        response=response,
        response_json=response_json,
        response_sha256=hashlib.sha256(response_json).hexdigest(),
        metadata=_receipt_metadata(request, response, occurred_at),
    )


class LocalBrowserReceiptStore:
    """Mutable single-writer authority for append-only browser receipts."""

    def __init__(self, path: Path, *, owner_id: int | None = None) -> None:
        self.path = path.absolute()
        self._owner_id = os.geteuid() if owner_id is None else owner_id
        self._connection: sqlite3.Connection | None = None
        self._database: PrivateBrowserReceiptDatabase | None = None

    def __enter__(self) -> LocalBrowserReceiptStore:
        if self._connection is not None:
            raise InvalidLocalBrowserReceiptError()
        try:
            database = open_private_browser_receipt_database(self.path, self._owner_id)
        except (InvalidLocalBrowserPrivateFsError, OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidLocalBrowserReceiptError() from None
        self._connection = database.connection
        self._database = database
        return self

    def __exit__(self, *_details: BaseException | None) -> None:
        self._connection = None
        database, self._database = self._database, None
        if database is not None:
            database.close()

    def replay(self, request_id: str, request_sha256: str) -> BrowserResponse | None:
        connection = self._require_open()
        request_row = connection.execute(
            "SELECT request_sha256 FROM local_browser_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if request_row is None:
            return None
        if request_row[0] != request_sha256:
            raise LocalBrowserReceiptConflictError()
        response_row = connection.execute(
            "SELECT response_json, response_sha256 FROM local_browser_responses WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if response_row is None:
            raise InvalidLocalBrowserReceiptError()
        payload = str(response_row[0]).encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != response_row[1]:
            raise InvalidLocalBrowserReceiptError()
        try:
            response = parse_browser_response(payload)
        except ValueError:
            raise InvalidLocalBrowserReceiptError() from None
        if response.request_id != request_id:
            raise InvalidLocalBrowserReceiptError()
        return response

    def append(self, receipt: BrowserReceipt) -> None:
        connection = self._require_open()
        replay = self.replay(receipt.request.request_id, receipt.request_sha256)
        if replay is not None:
            if replay != receipt.response:
                raise LocalBrowserReceiptConflictError()
            return
        metadata = receipt.metadata
        reason = receipt.response.failure.reason.value if receipt.response.failure is not None else None
        occurred_at = metadata.occurred_at.astimezone(UTC).isoformat()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO local_browser_requests VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        receipt.request.request_id,
                        request_action(receipt.request).value,
                        receipt.request_sha256,
                        metadata.target_id,
                        metadata.normalized_url,
                        occurred_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO local_browser_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt.request.request_id,
                        receipt.response_json.decode("ascii"),
                        receipt.response_sha256,
                        receipt.response.status,
                        reason,
                        metadata.target_id,
                        metadata.normalized_url,
                        metadata.observation_sha256,
                        metadata.screenshot_sha256,
                        occurred_at,
                    ),
                )
        except sqlite3.Error:
            raise InvalidLocalBrowserReceiptError() from None

    def _require_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise InvalidLocalBrowserReceiptError(reason="browser_receipt_store_closed")
        return self._connection


def _receipt_metadata(
    request: BrowserRequest, response: BrowserResponse, occurred_at: datetime
) -> BrowserReceiptMetadata:
    target_id, normalized_url = _request_target(request), _request_url(request)
    observation_sha256 = None
    if response.observation is not None:
        target_id, normalized_url = response.observation.target_id, response.observation.url
        payload = json.dumps(
            response.observation.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        ).encode()
        observation_sha256 = hashlib.sha256(payload).hexdigest()
    screenshot_sha256 = response.screenshot.sha256 if response.screenshot is not None else None
    return BrowserReceiptMetadata(
        target_id, normalized_url, observation_sha256, screenshot_sha256, occurred_at.astimezone(UTC)
    )


def _request_target(request: BrowserRequest) -> str | None:
    match request:
        case (
            BrowserReadRequest(target_id=target_id)
            | BrowserFollowRequest(target_id=target_id)
            | BrowserCaptureRequest(target_id=target_id)
        ):
            return target_id
        case BrowserStatusRequest() | BrowserSearchRequest() | BrowserOpenRequest():
            return None
        case unreachable:
            assert_never(unreachable)


def _request_url(request: BrowserRequest) -> str | None:
    match request:
        case BrowserOpenRequest(url=url):
            return url
        case (
            BrowserStatusRequest()
            | BrowserSearchRequest()
            | BrowserReadRequest()
            | BrowserFollowRequest()
            | BrowserCaptureRequest()
        ):
            return None
        case unreachable:
            assert_never(unreachable)
