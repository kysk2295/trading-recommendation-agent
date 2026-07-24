from __future__ import annotations

import datetime as dt
import socket
from collections.abc import Callable
from typing import Final, override

import httpx2

from trading_agent.arxiv_research_models import (
    ARXIV_MAX_RAW_BYTES,
    ArxivRawReceipt,
    ArxivResearchRequest,
)

ARXIV_BASE_URL: Final = "https://export.arxiv.org"
_PATH: Final = "/api/query"


class ArxivTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "arXiv research transport failed"


class ArxivResearchClient:
    __slots__ = ("_client", "_clock")

    def __init__(
        self,
        client: httpx2.Client,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != ARXIV_BASE_URL or client.follow_redirects:
            raise ArxivTransportError
        self._client = client
        self._clock = _clock

    def fetch(self, request: ArxivResearchRequest) -> ArxivRawReceipt:
        query = " AND ".join(
            (f"cat:{request.category}", *(f'all:"{term}"' for term in request.terms))
        )
        try:
            with self._client.stream(
                "GET",
                _PATH,
                params={
                    "search_query": query,
                    "start": "0",
                    "max_results": str(request.max_results),
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
                headers={
                    "Accept": "application/atom+xml",
                    "Accept-Encoding": "identity",
                    "User-Agent": "trading-recommendation-agent/research-knowledge",
                },
            ) as response:
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "export.arxiv.org"
                    or response.url.path != _PATH
                    or 300 <= response.status_code < 400
                ):
                    raise ArxivTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit() or int(declared) > ARXIV_MAX_RAW_BYTES
                ):
                    raise ArxivTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > ARXIV_MAX_RAW_BYTES:
                        raise ArxivTransportError
                    payload.extend(chunk)
                return ArxivRawReceipt.from_raw(
                    request_id=request.request_id,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, TypeError, ValueError):
            raise ArxivTransportError from None


def create_arxiv_http_client() -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=ARXIV_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )


def _content_type(response: httpx2.Response) -> str:
    return response.headers.get("content-type", "application/octet-stream").partition(";")[0].strip().lower()


__all__ = (
    "ARXIV_BASE_URL",
    "ArxivResearchClient",
    "ArxivTransportError",
    "create_arxiv_http_client",
)
