from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final, override

import httpx2

from trading_agent.ls_config import LS_REST_BASE_URL, LsCredentials

LS_TOKEN_PATH: Final = "/oauth2/token"
MAX_LS_TOKEN_RESPONSE_BYTES: Final = 65_536


class UnsafeLsTokenEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS OAuth endpoint는 공식 고정값이어야 합니다"


class UnsafeLsTokenRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS OAuth client는 redirect를 따라가면 안 됩니다"


class LsTokenTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "LS OAuth 전송에 실패했습니다"


class LsTokenResponseError(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return f"LS OAuth 응답이 유효하지 않습니다: {self.failure_code}"


@dataclass(frozen=True, slots=True)
class LsAccessToken:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_access_token(self.value):
            raise LsTokenResponseError("invalid_response")


def issue_ls_access_token(
    client: httpx2.Client,
    credentials: LsCredentials,
) -> LsAccessToken:
    if str(client.base_url).rstrip("/") != LS_REST_BASE_URL:
        raise UnsafeLsTokenEndpointError
    if client.follow_redirects:
        raise UnsafeLsTokenRedirectPolicyError
    try:
        response = client.post(
            LS_TOKEN_PATH,
            headers={"content-type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "appkey": credentials.app_key,
                "appsecretkey": credentials.app_secret,
                "scope": "oob",
            },
        )
    except httpx2.HTTPError:
        raise LsTokenTransportError from None
    if response.status_code != httpx2.codes.OK:
        raise LsTokenResponseError(f"http_{response.status_code}")
    if _response_content_type(response) != "application/json":
        raise LsTokenResponseError("content_type")
    payload = bytes(response.content)
    if not payload:
        raise LsTokenResponseError("empty_response")
    if len(payload) > MAX_LS_TOKEN_RESPONSE_BYTES:
        raise LsTokenResponseError("response_too_large")
    try:
        document: object = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise LsTokenResponseError("invalid_json") from None
    if not isinstance(document, dict):
        raise LsTokenResponseError("invalid_response")
    token = document.get("access_token")
    if not isinstance(token, str):
        raise LsTokenResponseError("invalid_response")
    return LsAccessToken(token)


def _response_content_type(response: httpx2.Response) -> str:
    return response.headers.get("content-type", "").partition(";")[0].strip().lower()


def _valid_access_token(value: str) -> bool:
    return (
        32 <= len(value) <= 4_096
        and value.isascii()
        and all(33 <= ord(character) <= 126 for character in value)
    )
