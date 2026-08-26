from __future__ import annotations

import pytest

from trading_agent.chrome_cdp_response import require_cdp_success
from trading_agent.chrome_devtools_types import InvalidChromeDevToolsError


def test_matching_response_with_result_object_is_accepted() -> None:
    body = b'{"id":7,"result":{"frameId":"frame-1"}}'

    assert require_cdp_success(body, 7) is body


@pytest.mark.parametrize(
    "body",
    (
        b'{"id":7,"error":{"code":-32601,"message":"provider-secret"}}',
        b'{"id":7,"result":{},"error":null}',
        b'{"id":7}',
        b'{"id":7,"result":null}',
        b'{"id":7,"result":[]}',
        b'{"id":7,"result":"ok"}',
        b'{"id":8,"result":{}}',
        b'{"id":0,"result":{}}',
        b"not-json",
    ),
)
def test_non_success_response_is_replaced_with_stable_reason(body: bytes) -> None:
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = require_cdp_success(body, 7)

    assert raised.value.reason == "browser_navigation_blocked"
    assert "provider-secret" not in str(raised.value)
