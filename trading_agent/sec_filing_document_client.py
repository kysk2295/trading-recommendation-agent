from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Final, final, override

import httpx2

from trading_agent.sec_edgar_client import (
    SecEdgarTransportError,
    _content_length,
    _request_deadline,
    _response_content_encoding,
    _response_content_type,
)
from trading_agent.sec_edgar_config import SEC_EDGAR_ARCHIVE_BASE_URL, SecUserAgent
from trading_agent.sec_filing_document_models import (
    SEC_FILING_DOCUMENT_MAX_RAW_BYTES,
    SecFilingDocumentRawResponse,
    SecFilingDocumentTarget,
)

MAX_SEC_FILING_DOCUMENT_BYTES: Final = SEC_FILING_DOCUMENT_MAX_RAW_BYTES


class UnsafeSecFilingDocumentEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC filing document client origin must be the official fixed endpoint"


class UnsafeSecFilingDocumentRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC filing document client must not follow redirects"


class SecFilingDocumentTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "SEC filing document transport failed"


@final
class SecFilingDocumentClient:
    __slots__ = ("_client", "_clock", "_user_agent")

    def __init__(
        self,
        client: httpx2.Client,
        user_agent: SecUserAgent,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != SEC_EDGAR_ARCHIVE_BASE_URL:
            raise UnsafeSecFilingDocumentEndpointError
        if client.follow_redirects:
            raise UnsafeSecFilingDocumentRedirectPolicyError
        self._client = client
        self._user_agent = user_agent
        self._clock = _clock

    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse:
        try:
            with (
                _request_deadline(),
                self._client.stream(
                    "GET",
                    target.archive_path,
                    headers={
                        "User-Agent": self._user_agent.value,
                        "Accept": "text/html, application/xhtml+xml, application/xml, text/plain, application/pdf",
                        "Accept-Encoding": "gzip, deflate",
                    },
                ) as response,
            ):
                content_length = _content_length(response)
                if content_length is not None and content_length > MAX_SEC_FILING_DOCUMENT_BYTES:
                    raise SecFilingDocumentTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > MAX_SEC_FILING_DOCUMENT_BYTES:
                        raise SecFilingDocumentTransportError
                    payload.extend(chunk)
                return SecFilingDocumentRawResponse(
                    target_id=target.target_id,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_response_content_type(response),
                    content_encoding=_response_content_encoding(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, SecEdgarTransportError, TimeoutError, ValueError):
            raise SecFilingDocumentTransportError from None
