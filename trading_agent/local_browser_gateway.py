from __future__ import annotations

import hashlib

from trading_agent.local_browser_dispatch import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
    LoopbackBrowserClientFactory,
)
from trading_agent.local_browser_gateway_wire import (
    BrowserRequest,
    InvalidLocalBrowserWireError,
    canonical_browser_request,
    canonical_browser_response,
    parse_browser_request,
    parse_browser_response,
)
from trading_agent.local_browser_protocol import BrowserResponse
from trading_agent.local_browser_receipts import (
    InvalidLocalBrowserReceiptError,
    LocalBrowserReceiptConflictError,
    LocalBrowserReceiptStore,
    browser_receipt,
)


class InvalidLocalBrowserGatewayError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class LocalBrowserGateway:
    def __init__(self, receipt_store: LocalBrowserReceiptStore, dispatcher: BrowserRequestDispatcher) -> None:
        self._receipt_store = receipt_store
        self._dispatcher = dispatcher

    def handle(self, request: BrowserRequest) -> BrowserResponse:
        canonical = canonical_browser_request(request)
        request_sha256 = hashlib.sha256(canonical).hexdigest()
        try:
            with self._receipt_store.execution_lease():
                replay = self._receipt_store.replay(request.request_id, request_sha256)
                if replay is not None:
                    return replay
                response = self._dispatcher.dispatch(request)
                self._receipt_store.append(browser_receipt(request, response, self._dispatcher.now()))
                return response
        except (InvalidLocalBrowserReceiptError, LocalBrowserReceiptConflictError) as error:
            raise InvalidLocalBrowserGatewayError(reason=error.reason) from None

    def handle_bytes(self, payload: bytes) -> bytes:
        try:
            return canonical_browser_response(self.handle(parse_browser_request(payload)))
        except InvalidLocalBrowserWireError as error:
            raise InvalidLocalBrowserGatewayError(reason=error.reason) from None


__all__ = [
    "BrowserDispatchDependencies",
    "BrowserRequestDispatcher",
    "InvalidLocalBrowserGatewayError",
    "LocalBrowserGateway",
    "LoopbackBrowserClientFactory",
    "canonical_browser_request",
    "canonical_browser_response",
    "parse_browser_request",
    "parse_browser_response",
]
