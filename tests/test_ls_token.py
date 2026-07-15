from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx2
import pytest

from trading_agent.ls_config import LS_REST_BASE_URL, LsCredentials
from trading_agent.ls_token import (
    LsAccessToken,
    LsTokenResponseError,
    LsTokenTransportError,
    UnsafeLsTokenEndpointError,
    UnsafeLsTokenRedirectPolicyError,
    issue_ls_access_token,
)

APP_KEY = "k" * 40
APP_SECRET = "s" * 40
ACCESS_TOKEN = "t" * 64
PRIVATE_MESSAGE = "private-provider-message"


def test_ls_oauth_posts_exact_form_without_query_secret() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            json={
                "access_token": ACCESS_TOKEN,
                "scope": "oob",
                "token_type": "Bearer",
                "expires_in": 86_400,
            },
        )

    with _client(handle) as client:
        token = issue_ls_access_token(client, _credentials())

    assert token.value == ACCESS_TOKEN
    assert ACCESS_TOKEN not in repr(token)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{LS_REST_BASE_URL}/oauth2/token"
    assert request.url.query == b""
    assert request.headers["content-type"].startswith(
        "application/x-www-form-urlencoded"
    )
    assert parse_qs(request.content.decode("ascii"), strict_parsing=True) == {
        "grant_type": ["client_credentials"],
        "appkey": [APP_KEY],
        "appsecretkey": [APP_SECRET],
        "scope": ["oob"],
    }


@pytest.mark.parametrize(
    "base_url",
    (
        "https://openapi.ls-sec.co.kr",
        "http://openapi.ls-sec.co.kr:8080",
        "https://openapi.ls-sec.co.kr.evil.example:8080",
        f"{LS_REST_BASE_URL}/oauth2",
    ),
)
def test_ls_oauth_rejects_every_noncanonical_base_url(base_url: str) -> None:
    with (
        httpx2.Client(
            base_url=base_url,
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, request=request)
            ),
            follow_redirects=False,
        ) as client,
        pytest.raises(UnsafeLsTokenEndpointError),
    ):
        _ = issue_ls_access_token(client, _credentials())


def test_ls_oauth_rejects_redirect_client_before_request() -> None:
    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, request=request)

    with (
        httpx2.Client(
            base_url=LS_REST_BASE_URL,
            transport=httpx2.MockTransport(handle),
            follow_redirects=True,
        ) as client,
        pytest.raises(UnsafeLsTokenRedirectPolicyError),
    ):
        _ = issue_ls_access_token(client, _credentials())

    assert calls == 0


@pytest.mark.parametrize(
    ("status_code", "failure_code"),
    ((400, "http_400"), (401, "http_401"), (429, "http_429"), (500, "http_500")),
)
def test_ls_oauth_sanitizes_http_failures(
    status_code: int,
    failure_code: str,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code,
            request=request,
            headers={"content-type": "application/json"},
            json={"rsp_msg": PRIVATE_MESSAGE, "secret": APP_SECRET},
        )

    with (
        _client(handle) as client,
        pytest.raises(LsTokenResponseError) as captured,
    ):
        _ = issue_ls_access_token(client, _credentials())

    assert captured.value.failure_code == failure_code
    _assert_private_markers_absent(str(captured.value))


@pytest.mark.parametrize(
    ("content_type", "content", "failure_code"),
    (
        ("text/plain", b"private", "content_type"),
        ("application/json", b"", "empty_response"),
        ("application/json", b"{" + b"x" * 70_000, "response_too_large"),
        ("application/json", b"{not-json", "invalid_json"),
        ("application/json", b"[]", "invalid_response"),
        ("application/json", b"{}", "invalid_response"),
        (
            "application/json",
            b'{"access_token":"short"}',
            "invalid_response",
        ),
        (
            "application/json",
            b'{"access_token":"token with spaces token with spaces"}',
            "invalid_response",
        ),
    ),
)
def test_ls_oauth_rejects_invalid_success_response_without_rendering_it(
    content_type: str,
    content: bytes,
    failure_code: str,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": content_type},
            content=content,
        )

    with (
        _client(handle) as client,
        pytest.raises(LsTokenResponseError) as captured,
    ):
        _ = issue_ls_access_token(client, _credentials())

    assert captured.value.failure_code == failure_code
    _assert_private_markers_absent(str(captured.value))


def test_ls_oauth_converts_transport_error_to_sanitized_error() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(
            f"{PRIVATE_MESSAGE}:{APP_SECRET}",
            request=request,
        )

    with (
        _client(handle) as client,
        pytest.raises(LsTokenTransportError) as captured,
    ):
        _ = issue_ls_access_token(client, _credentials())

    _assert_private_markers_absent(str(captured.value))


@pytest.mark.parametrize(
    "value",
    ("short", "t" * 4097, "t" * 31 + " ", "t" * 31 + "한"),
)
def test_ls_access_token_rejects_invalid_direct_construction(value: str) -> None:
    with pytest.raises(LsTokenResponseError):
        _ = LsAccessToken(value)


def _credentials() -> LsCredentials:
    return LsCredentials(APP_KEY, APP_SECRET)


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.Client:
    return httpx2.Client(
        base_url=LS_REST_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )


def _assert_private_markers_absent(rendered: str) -> None:
    for marker in (APP_KEY, APP_SECRET, ACCESS_TOKEN, PRIVATE_MESSAGE):
        assert marker not in rendered
