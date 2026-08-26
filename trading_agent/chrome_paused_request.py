from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.local_browser_protocol import InvalidLocalBrowserProtocolError, require_public_https_url


@dataclass(frozen=True, slots=True)
class InvalidPausedRequestIdentityError(RuntimeError):
    reason: str = "browser_navigation_blocked"

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PausedRequest:
    request_id: str
    allowed: bool


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, hide_input_in_errors=True)


class _IdentityParams(_BoundaryModel):
    request_id: str = Field(alias="requestId", min_length=1, max_length=256)


class _IdentityEnvelope(_BoundaryModel):
    method: Literal["Fetch.requestPaused"]
    params: _IdentityParams


class _UrlRequest(_BoundaryModel):
    url: str = Field(min_length=1, max_length=2_048)


class _UrlParams(_BoundaryModel):
    request: _UrlRequest


class _UrlEnvelope(_BoundaryModel):
    params: _UrlParams


def parse_paused_request(body: bytes) -> PausedRequest:
    try:
        identity = _IdentityEnvelope.model_validate_json(body).params
    except ValidationError:
        raise InvalidPausedRequestIdentityError() from None
    try:
        url = _UrlEnvelope.model_validate_json(body).params.request.url
        _ = require_public_https_url(url)
    except (InvalidLocalBrowserProtocolError, ValidationError):
        allowed = False
    else:
        allowed = True
    return PausedRequest(identity.request_id, allowed)


__all__ = ["InvalidPausedRequestIdentityError", "PausedRequest", "parse_paused_request"]
