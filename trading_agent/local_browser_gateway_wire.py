from __future__ import annotations

import json
from typing import Annotated, Final, Literal, assert_never

from pydantic import Field, TypeAdapter, ValidationError

from trading_agent.local_browser_protocol import (
    MAX_RESPONSE_BYTES,
    BrowserAction,
    BrowserCaptureRequest,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserReadRequest,
    BrowserResponse,
    BrowserSearchRequest,
    BrowserStatusRequest,
)

MAX_REQUEST_BYTES: Final = 16 * 1024
type BrowserRequest = (
    BrowserStatusRequest
    | BrowserSearchRequest
    | BrowserOpenRequest
    | BrowserReadRequest
    | BrowserFollowRequest
    | BrowserCaptureRequest
)


class InvalidLocalBrowserWireError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _StatusWire(BrowserStatusRequest):
    action: Literal[BrowserAction.STATUS]


class _SearchWire(BrowserSearchRequest):
    action: Literal[BrowserAction.SEARCH]


class _OpenWire(BrowserOpenRequest):
    action: Literal[BrowserAction.OPEN]


class _ReadWire(BrowserReadRequest):
    action: Literal[BrowserAction.READ]


class _FollowWire(BrowserFollowRequest):
    action: Literal[BrowserAction.FOLLOW]


class _CaptureWire(BrowserCaptureRequest):
    action: Literal[BrowserAction.CAPTURE]


type _WireRequest = Annotated[
    _StatusWire | _SearchWire | _OpenWire | _ReadWire | _FollowWire | _CaptureWire,
    Field(discriminator="action"),
]
_WIRE_ADAPTER: Final = TypeAdapter(_WireRequest)


def request_action(request: BrowserRequest) -> BrowserAction:
    match request:
        case BrowserStatusRequest():
            return BrowserAction.STATUS
        case BrowserSearchRequest():
            return BrowserAction.SEARCH
        case BrowserOpenRequest():
            return BrowserAction.OPEN
        case BrowserReadRequest():
            return BrowserAction.READ
        case BrowserFollowRequest():
            return BrowserAction.FOLLOW
        case BrowserCaptureRequest():
            return BrowserAction.CAPTURE
        case unreachable:
            assert_never(unreachable)


def canonical_browser_request(request: BrowserRequest) -> bytes:
    payload = request.model_dump(mode="json")
    payload["action"] = request_action(request).value
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def parse_browser_request(payload: bytes) -> BrowserRequest:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b"\n" in payload:
        raise InvalidLocalBrowserWireError(reason="browser_request_invalid")
    try:
        wire = _WIRE_ADAPTER.validate_json(payload, strict=True)
        request = _request_from_wire(wire)
    except (TypeError, ValidationError, ValueError):
        raise InvalidLocalBrowserWireError(reason="browser_request_invalid") from None
    if payload != canonical_browser_request(request):
        raise InvalidLocalBrowserWireError(reason="browser_request_noncanonical")
    return request


def canonical_browser_response(response: BrowserResponse) -> bytes:
    payload = json.dumps(
        response.model_dump(mode="json", exclude_unset=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise InvalidLocalBrowserWireError(reason="browser_response_too_large")
    return payload


def parse_browser_response(payload: bytes) -> BrowserResponse:
    if not payload or len(payload) > MAX_RESPONSE_BYTES or b"\n" in payload:
        raise InvalidLocalBrowserWireError(reason="browser_response_invalid")
    try:
        response = BrowserResponse.model_validate_json(payload, strict=True)
    except (TypeError, ValidationError, ValueError):
        raise InvalidLocalBrowserWireError(reason="browser_response_invalid") from None
    if payload != canonical_browser_response(response):
        raise InvalidLocalBrowserWireError(reason="browser_response_noncanonical")
    return response


def _request_from_wire(wire: _WireRequest) -> BrowserRequest:
    match wire:
        case _StatusWire(request_id=request_id):
            return BrowserStatusRequest(request_id=request_id)
        case _SearchWire(request_id=request_id, query=query):
            return BrowserSearchRequest(request_id=request_id, query=query)
        case _OpenWire(request_id=request_id, url=url):
            return BrowserOpenRequest(request_id=request_id, url=url)
        case _ReadWire(request_id=request_id, target_id=target_id):
            return BrowserReadRequest(request_id=request_id, target_id=target_id)
        case _FollowWire(request_id=request_id, target_id=target_id, link_index=link_index):
            return BrowserFollowRequest(request_id=request_id, target_id=target_id, link_index=link_index)
        case _CaptureWire(request_id=request_id, target_id=target_id):
            return BrowserCaptureRequest(request_id=request_id, target_id=target_id)
        case unreachable:
            assert_never(unreachable)
