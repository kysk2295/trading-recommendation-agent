from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2
import pytest

import trading_agent.kis_auth as kis_auth
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


def test_cached_only_loader_returns_current_private_token_without_transport(tmp_path: Path) -> None:
    # Given: a current token in the exact private cache file contract.
    _token_cache(tmp_path, expires_at="2026-07-14T00:00:00+00:00")

    # When: the cached-only boundary is read without any HTTP client.
    token = kis_auth.load_cached_kis_access_token(
        KisMode.LIVE,
        cache_dir=tmp_path,
        now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
    )

    # Then: the cached token is returned without refresh capability.
    assert token == "cached-token"


def test_cached_only_loader_rejects_symlinked_cache_directory(tmp_path: Path) -> None:
    # Given: a private cache reached only through a symlinked directory.
    actual_cache = tmp_path / "actual-cache"
    actual_cache.mkdir(mode=0o700)
    _token_cache(actual_cache, expires_at="2026-07-14T00:00:00+00:00")
    linked_cache = tmp_path / "linked-cache"
    linked_cache.symlink_to(actual_cache, target_is_directory=True)

    # When/Then: the cached-only reader rejects the directory identity without exposing its path.
    with pytest.raises(kis_auth.InvalidKisTokenCacheError) as captured:
        _ = kis_auth.load_cached_kis_access_token(
            KisMode.LIVE,
            cache_dir=linked_cache,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )
    assert captured.value.reason is kis_auth.KisTokenCacheErrorReason.UNSAFE
    assert str(tmp_path) not in str(captured.value)


@pytest.mark.parametrize("case", ("missing", "stale", "malformed", "wrong_mode", "wrong_owner", "symlink"))
def test_cached_only_loader_fails_safely_for_untrusted_cache(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a missing, stale, malformed, non-private, or symlink token cache.
    cache = tmp_path / "kis-live-token.json"
    if case == "stale":
        _token_cache(tmp_path, expires_at="2026-07-13T00:04:00+00:00")
    elif case == "malformed":
        cache.write_text('{"access_token":"PRIVATE-token"}', encoding="utf-8")
        cache.chmod(0o600)
    elif case == "wrong_mode":
        _token_cache(tmp_path, expires_at="2026-07-14T00:00:00+00:00")
        cache.chmod(0o640)
    elif case == "wrong_owner":
        _token_cache(tmp_path, expires_at="2026-07-14T00:00:00+00:00")
        monkeypatch.setattr(kis_auth.os, "getuid", lambda: cache.stat().st_uid + 1)
    elif case == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        cache.symlink_to(target)

    # When/Then: one stable error exposes no path, token, or authentication body.
    with pytest.raises(kis_auth.InvalidKisTokenCacheError) as captured:
        _ = kis_auth.load_cached_kis_access_token(
            KisMode.LIVE,
            cache_dir=tmp_path,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )
    message = str(captured.value)
    assert str(tmp_path) not in message
    assert "PRIVATE" not in message and "token" not in message.lower()


def test_legacy_get_access_token_refreshes_only_after_cache_miss(tmp_path: Path) -> None:
    # Given: the legacy refresh boundary has no cached token.
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"access_token": "refreshed-token"})

    # When: the existing refresh-capable API is called explicitly.
    cache_dir = tmp_path / "new-cache"
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handler),
    ) as client:
        token = get_access_token(
            client,
            KisCredentials("key", "secret"),
            KisMode.LIVE,
            cache_dir=cache_dir,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )

    # Then: only that API issues the reviewed authentication request and persists privately.
    assert token == "refreshed-token"
    assert len(seen) == 1 and seen[0].method == "POST" and seen[0].url.path == "/oauth2/tokenP"
    assert cache_dir.stat().st_mode & 0o777 == 0o700
    assert (cache_dir / "kis-live-token.json").stat().st_mode & 0o777 == 0o600


def test_legacy_refresh_publishes_through_verified_directory_after_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the verified cache pathname is replaced with an attacker directory before publication.
    cache_dir = tmp_path / "cache"
    retained_dir = tmp_path / "retained-cache"
    attacker_dir = tmp_path / "attacker-cache"
    attacker_dir.mkdir(mode=0o700)
    original_require = kis_auth.require_private_directory

    def swap_cache_path(descriptor: int) -> None:
        original_require(descriptor)
        cache_dir.rename(retained_dir)
        cache_dir.symlink_to(attacker_dir, target_is_directory=True)

    monkeypatch.setattr(kis_auth, "require_private_directory", swap_cache_path)

    # When: the legacy API refreshes and publishes its token.
    seen: list[httpx2.Request] = []
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(
            lambda request: seen.append(request) or httpx2.Response(200, json={"access_token": "refreshed-token"})
        ),
    ) as client:
        token = get_access_token(
            client,
            KisCredentials("key", "secret"),
            KisMode.LIVE,
            cache_dir=cache_dir,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )

    # Then: only the retained directory receives one private final token file.
    assert token == "refreshed-token" and len(seen) == 1
    assert json.loads((retained_dir / "kis-live-token.json").read_text())["access_token"] == token
    assert tuple(attacker_dir.iterdir()) == ()


def test_legacy_refresh_ignores_preexisting_predictable_temp_symlink(tmp_path: Path) -> None:
    # Given: the former predictable temporary basename points at an attacker-controlled file.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o700)
    attacker_file = tmp_path / "attacker.txt"
    attacker_file.write_text("unchanged", encoding="utf-8")
    attacker_file.chmod(0o600)
    (cache_dir / "kis-live-token.tmp").symlink_to(attacker_file)

    # When: the legacy API refreshes and publishes its token.
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"access_token": "refreshed-token"})),
    ) as client:
        token = get_access_token(
            client,
            KisCredentials("key", "secret"),
            KisMode.LIVE,
            cache_dir=cache_dir,
            now=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        )

    # Then: the attacker file is untouched and the final private token is published independently.
    assert attacker_file.read_text(encoding="utf-8") == "unchanged"
    assert json.loads((cache_dir / "kis-live-token.json").read_text())["access_token"] == token
    assert (cache_dir / "kis-live-token.json").stat().st_mode & 0o777 == 0o600


def _token_cache(path: Path, *, expires_at: str) -> None:
    cache = path / "kis-live-token.json"
    cache.write_text(json.dumps({"access_token": "cached-token", "expires_at": expires_at}), encoding="utf-8")
    cache.chmod(0o600)
