from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import socket
import stat
from contextlib import suppress
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
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    open_private_parent,
    require_private_directory,
    require_private_directory_query_only,
)


class KisMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"


class KisTokenCacheErrorReason(StrEnum):
    MISSING = "missing"
    STALE = "stale"
    MALFORMED = "malformed"
    UNSAFE = "unsafe"


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


class InvalidKisTokenCacheError(RuntimeError):
    __slots__ = ("reason",)

    reason: KisTokenCacheErrorReason

    def __init__(self, reason: KisTokenCacheErrorReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

    @override
    def __str__(self) -> str:
        return f"KIS cache unavailable: {self.reason.value}"


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
        follow_redirects=False,
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
    try:
        return load_cached_kis_access_token(mode, cache_dir=cache_dir, now=checked_at)
    except InvalidKisTokenCacheError as error:
        match error.reason:
            case KisTokenCacheErrorReason.MISSING | KisTokenCacheErrorReason.STALE:
                pass
            case KisTokenCacheErrorReason.UNSAFE:
                try:
                    mode_bits = stat.S_IMODE(cache_path.lstat().st_mode)
                except OSError:
                    mode_bits = 0
                raise UnsafeSecretFileError(path=cache_path, mode=mode_bits) from None
            case KisTokenCacheErrorReason.MALFORMED:
                raise
            case unreachable:
                assert_never(unreachable)
    token = issue_access_token(client, credentials)
    directory_descriptor = open_private_parent(cache_dir, create=True)
    try:
        require_private_directory(directory_descriptor)
        payload = json.dumps(
            {
                "access_token": token,
                "expires_at": (checked_at + dt.timedelta(hours=23)).isoformat(),
            }
        )
        _publish_cached_token(directory_descriptor, f"kis-{mode.value}-token.json", payload)
    finally:
        os.close(directory_descriptor)
    return token


def load_cached_kis_access_token(
    mode: KisMode,
    cache_dir: Path = DEFAULT_TOKEN_DIR,
    now: dt.datetime | None = None,
) -> str:
    checked_at = dt.datetime.now(dt.UTC) if now is None else now.astimezone(dt.UTC)
    try:
        directory_descriptor = open_private_parent(cache_dir, create=False)
    except FileNotFoundError:
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.MISSING) from None
    except (InvalidPrivateDirectoryIdentityError, OSError):
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.UNSAFE) from None
    try:
        require_private_directory_query_only(directory_descriptor)
        descriptor = os.open(
            f"kis-{mode.value}-token.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
    except FileNotFoundError:
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.MISSING) from None
    except (InvalidPrivateDirectoryIdentityError, OSError):
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.UNSAFE) from None
    finally:
        os.close(directory_descriptor)
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.UNSAFE)
            payload = handle.read()
        cached = TOKEN_CACHE_ADAPTER.validate_json(payload)
        expires_at = dt.datetime.fromisoformat(cached["expires_at"])
        access_token = cached["access_token"]
    except InvalidKisTokenCacheError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.MALFORMED) from None
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or not access_token.strip()
        or access_token != access_token.strip()
    ):
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.MALFORMED)
    if expires_at <= checked_at + dt.timedelta(minutes=5):
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.STALE)
    return access_token


def _publish_cached_token(directory_descriptor: int, final_name: str, payload: str) -> None:
    temporary_name = f".{final_name}.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            final_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    except OSError:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        raise InvalidKisTokenCacheError(KisTokenCacheErrorReason.UNSAFE) from None


def quote_headers(credentials: KisCredentials, access_token: str, transaction_id: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {access_token}",
        "appkey": credentials.app_key,
        "appsecret": credentials.app_secret,
        "tr_id": transaction_id,
        "custtype": "P",
    }
