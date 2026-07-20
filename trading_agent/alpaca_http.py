from __future__ import annotations

import resource
import socket
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

ALPACA_DATA_URL: Final = "https://data.alpaca.markets"
DEFAULT_ALPACA_SECRET_PATH: Final = Path.home() / ".config/trading-agent/alpaca.env"


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)


class AlpacaSecretFileError(PermissionError):
    __slots__ = ("mode", "path")

    def __init__(self, path: Path, mode: int) -> None:
        super().__init__()
        self.path = path
        self.mode = mode

    @override
    def __str__(self) -> str:
        return f"Alpaca 비밀 파일 권한은 600이어야 합니다: {self.path} ({self.mode:o})"


class MissingAlpacaCredentialsError(RuntimeError):
    __slots__ = ("names",)

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self.names = names

    @override
    def __str__(self) -> str:
        return f"Alpaca 자격증명이 없습니다: {', '.join(self.names)}"


class AlpacaApiError(RuntimeError):
    __slots__ = ("message", "status_code")

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__()
        self.status_code = status_code
        self.message = message

    @override
    def __str__(self) -> str:
        return f"Alpaca API 오류 {self.status_code}: {self.message}"


class AlpacaMemoryLimitError(MemoryError):
    __slots__ = ("limit_gib", "rss_gib")

    def __init__(self, rss_gib: float, limit_gib: float) -> None:
        super().__init__()
        self.rss_gib = rss_gib
        self.limit_gib = limit_gib

    @override
    def __str__(self) -> str:
        return f"Alpaca 수집 RSS {self.rss_gib:.2f}GiB가 제한 {self.limit_gib:.2f}GiB에 도달했습니다"


def load_alpaca_credentials(path: Path = DEFAULT_ALPACA_SECRET_PATH) -> AlpacaCredentials:
    file_mode = stat.S_IMODE(path.stat().st_mode)
    if file_mode & 0o077:
        raise AlpacaSecretFileError(path=path, mode=file_mode)
    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            name, separator, value = raw_line.rstrip("\n").partition("=")
            if separator:
                values[name] = value.strip()
    key_id = values.get("APCA_API_KEY_ID", "")
    secret_key = values.get("APCA_API_SECRET_KEY", "")
    missing = tuple(
        name
        for name, value in (
            ("APCA_API_KEY_ID", key_id),
            ("APCA_API_SECRET_KEY", secret_key),
        )
        if value == ""
    )
    if missing:
        raise MissingAlpacaCredentialsError(names=missing)
    return AlpacaCredentials(key_id=key_id, secret_key=secret_key)


def create_alpaca_client(base_url: str = ALPACA_DATA_URL) -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=200,
        max_keepalive_connections=40,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=base_url,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=True,
    )


def create_alpaca_news_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=50,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=0,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )


def peak_rss_gib() -> float:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    bytes_used = peak if sys.platform == "darwin" else peak * 1024.0
    return bytes_used / (1024.0**3)
