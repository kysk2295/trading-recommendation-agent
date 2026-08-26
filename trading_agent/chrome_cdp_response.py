from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.chrome_devtools_types import InvalidChromeDevToolsError


class _CdpResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", frozen=True, hide_input_in_errors=True)


class _CdpSuccessResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)

    request_id: int = Field(alias="id", ge=1)
    result: _CdpResult


def require_cdp_success(body: bytes, expected_request_id: int) -> bytes:
    try:
        response = _CdpSuccessResponse.model_validate_json(body)
    except ValidationError:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
    if response.request_id != expected_request_id:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    return body


__all__ = ["require_cdp_success"]
