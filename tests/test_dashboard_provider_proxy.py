from __future__ import annotations

import pytest

from trading_agent.dashboard_provider_proxy import (
    InvalidProviderProxyRequestError,
    connect_target,
    restricted_provider_proxy,
)


@pytest.mark.parametrize(
    "host",
    [
        "api.openai.com",
        "chatgpt.com",
        "openrouter.ai",
    ],
)
def test_provider_proxy_accepts_only_declared_https_hosts(host: str) -> None:
    request = f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()

    assert connect_target(request) == (host, 443)


@pytest.mark.parametrize(
    "payload",
    [
        b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
        b"CONNECT openrouter.ai:80 HTTP/1.1\r\n\r\n",
        b"GET https://openrouter.ai/ HTTP/1.1\r\n\r\n",
        b"invalid\r\n\r\n",
    ],
)
def test_provider_proxy_rejects_non_provider_or_non_connect_traffic(
    payload: bytes,
) -> None:
    with pytest.raises(InvalidProviderProxyRequestError):
        connect_target(payload)


def test_provider_proxy_uses_loopback_ephemeral_endpoint() -> None:
    with restricted_provider_proxy() as proxy:
        assert proxy.url == f"http://127.0.0.1:{proxy.port}"
        assert proxy.port > 0
