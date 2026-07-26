from __future__ import annotations

import pytest

from trading_agent.dashboard_outbound_redaction import (
    UnsafeOutboundAgentEventError,
    redact_outbound_text,
    require_safe_outbound_text,
)


@pytest.mark.parametrize(
    "canary",
    [
        "authorization bearer-value",
        "session_id session-canary",
        "/Users/private/worktree/output.json",
        "raw_payload provider-body",
        "raw log provider-canary",
        "account_fingerprint identity",
        "account number 12345678",
        "~/.config/trading-agent/private.env",
    ],
)
def test_recursive_outbound_canaries_are_redacted(canary: str) -> None:
    # Given: a private canary in autonomous process output
    # When: it crosses the final public-text boundary
    redacted = redact_outbound_text(canary)

    # Then: the public validator accepts the replacement and rejects the original
    require_safe_outbound_text(redacted)
    assert all(token not in redacted for token in ("bearer-value", "session-canary", "provider-body", "identity"))
    with pytest.raises(UnsafeOutboundAgentEventError):
        require_safe_outbound_text(canary)
