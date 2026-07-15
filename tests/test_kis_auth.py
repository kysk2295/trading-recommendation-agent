from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    get_access_token,
)


def test_production_kis_clients_do_not_follow_redirects() -> None:
    for mode in KisMode:
        with create_kis_client(mode) as client:
            assert client.follow_redirects is False


def test_get_access_token_rejects_world_readable_cache(tmp_path: Path) -> None:
    cache = tmp_path / "kis-live-token.json"
    cache.write_text(
        json.dumps(
            {
                "access_token": "cached-token",
                "expires_at": "2026-07-14T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    cache.chmod(0o644)

    with (
        httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(500))) as client,
        pytest.raises(UnsafeSecretFileError, match="600"),
    ):
        _ = get_access_token(
            client,
            KisCredentials("key", "secret"),
            KisMode.LIVE,
            cache_dir=tmp_path,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )
