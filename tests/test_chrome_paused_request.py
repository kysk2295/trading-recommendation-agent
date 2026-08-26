from __future__ import annotations

import json

import pytest

from trading_agent.chrome_paused_request import (
    InvalidPausedRequestIdentityError,
    PausedRequest,
    parse_paused_request,
)


def _event(params_json: str) -> bytes:
    return f'{{"method":"Fetch.requestPaused","params":{params_json}}}'.encode()


def test_paused_request_projects_only_identity_and_url_policy() -> None:
    params = json.dumps(
        {
            "requestId": "intercept-1",
            "request": {
                "url": "https://example.com/story",
                "headers": {"Authorization": "must-not-cross-boundary"},
                "cookies": [{"name": "session", "value": "must-not-cross-boundary"}],
            },
        }
    )
    result = parse_paused_request(_event(params))
    assert result == PausedRequest("intercept-1", True)
    assert "must-not-cross-boundary" not in repr(result)


@pytest.mark.parametrize(
    "request_json",
    (
        json.dumps({"requestId": "intercept-1", "request": {"url": "https://example.com/" + "x" * 2_100}}),
        json.dumps({"requestId": "intercept-1", "request": {}}),
        json.dumps({"requestId": "intercept-1", "request": {"url": None}}),
    ),
)
def test_paused_request_retains_identity_when_url_is_invalid(request_json: str) -> None:
    assert parse_paused_request(_event(request_json)) == PausedRequest("intercept-1", False)


@pytest.mark.parametrize(
    "request_json",
    (
        json.dumps({"requestId": "x" * 257, "request": {"url": "https://example.com"}}),
        json.dumps({"requestId": None, "request": {"url": "https://example.com"}}),
        json.dumps({"request": {"url": "https://example.com"}}),
    ),
)
def test_paused_request_rejects_untrustworthy_identity(request_json: str) -> None:
    with pytest.raises(InvalidPausedRequestIdentityError):
        _ = parse_paused_request(_event(request_json))
