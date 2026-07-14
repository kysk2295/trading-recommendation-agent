from __future__ import annotations

import datetime as dt
import json
import os
import socket
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypedDict, assert_never, override

import httpx2
from pydantic import TypeAdapter

from scr_backtest.kis_intraday import (
    KisCredentials,
    MissingKisCredentialsError,
    issue_access_token,
)


class KisMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"


class KisTokenCachePayload(TypedDict):
    access_token: str
    expires_at: str


TOKEN_CACHE_ADAPTER: Final = TypeAdapter(KisTokenCachePayload)
DEFAULT_SECRET_PATH: Final = Path.home() / ".config/trading-agent/kis.env"
DEFAULT_TOKEN_DIR: Final = Path.home() / ".cache/trading-agent"


@dataclass(frozen=True, slots=True)
class UnsafeSecretFileError(PermissionError):
    path: Path
    mode: int

    @override
    def __str__(self) -> str:
        return f"비밀 파일 권한은 600이어야 합니다: {self.path} ({self.mode:o})"


def load_kis_credentials(mode: KisMode, path: Path = DEFAULT_SECRET_PATH) -> KisCredentials:
    file_mode = stat.S_IMODE(path.stat().st_mode)
    if file_mode & 0o077:
        raise UnsafeSecretFileError(path=path, mode=file_mode)
    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            name, separator, value = raw_line.rstrip("\n").partition("=")
            if separator:
                values[name] = value
    match mode:
        case KisMode.LIVE:
            prefix = "KIS_LIVE"
        case KisMode.PAPER:
            prefix = "KIS_PAPER"
        case unreachable:
            assert_never(unreachable)
    app_key = values.get(f"{prefix}_APP_KEY", "").strip()
    app_secret = values.get(f"{prefix}_APP_SECRET", "").strip()
    missing = tuple(name for name, value in (("KIS_APP_KEY", app_key), ("KIS_APP_SECRET", app_secret)) if value == "")
    if missing:
        raise MissingKisCredentialsError(missing_names=missing)
    return KisCredentials(app_key=app_key, app_secret=app_secret)


def create_kis_client(mode: KisMode) -> httpx2.Client:
    match mode:
        case KisMode.LIVE:
            base_url = "https://openapi.koreainvestment.com:9443"
        case KisMode.PAPER:
            base_url = "https://openapivts.koreainvestment.com:29443"
        case unreachable:
            assert_never(unreachable)
    limits = httpx2.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=base_url,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=True,
    )


def get_access_token(
    client: httpx2.Client,
    credentials: KisCredentials,
    mode: KisMode,
    cache_dir: Path = DEFAULT_TOKEN_DIR,
    now: dt.datetime | None = None,
) -> str:
    checked_at = dt.datetime.now(dt.UTC) if now is None else now.astimezone(dt.UTC)
    cache_path = cache_dir / f"kis-{mode.value}-token.json"
    if cache_path.is_file():
        file_mode = stat.S_IMODE(cache_path.stat().st_mode)
        if file_mode & 0o077:
            raise UnsafeSecretFileError(path=cache_path, mode=file_mode)
        cached = TOKEN_CACHE_ADAPTER.validate_json(cache_path.read_text(encoding="utf-8"))
        expires_at = dt.datetime.fromisoformat(cached["expires_at"])
        if expires_at > checked_at + dt.timedelta(minutes=5):
            return cached["access_token"]
    token = issue_access_token(client, credentials)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "access_token": token,
            "expires_at": (checked_at + dt.timedelta(hours=23)).isoformat(),
        }
    )
    temporary = cache_path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        _ = handle.write(payload)
    temporary.replace(cache_path)
    return token


def quote_headers(credentials: KisCredentials, access_token: str, transaction_id: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {access_token}",
        "appkey": credentials.app_key,
        "appsecret": credentials.app_secret,
        "tr_id": transaction_id,
        "custtype": "P",
    }
