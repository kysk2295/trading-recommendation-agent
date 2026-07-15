from __future__ import annotations

import os
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

from trading_agent.alpaca_paper_contract import ALPACA_PAPER_TRADING_URL

DEFAULT_ALPACA_PAPER_SECRET_PATH: Final = Path.home() / ".config/trading-agent/alpaca-paper.env"


class NonPaperTradingEndpointError(ValueError):
    __slots__ = ("url",)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @override
    def __str__(self) -> str:
        return "Alpaca 거래 주소는 paper 전용 고정값이어야 합니다"


def require_paper_trading_url(url: str) -> str:
    if url != ALPACA_PAPER_TRADING_URL:
        raise NonPaperTradingEndpointError(url)
    return url


@dataclass(frozen=True, slots=True)
class AlpacaPaperCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)


class AlpacaPaperSecretFileError(PermissionError):
    __slots__ = ("mode", "path")

    def __init__(self, path: Path, mode: int) -> None:
        super().__init__()
        self.path = path
        self.mode = mode

    @override
    def __str__(self) -> str:
        return f"Alpaca paper 비밀 파일 권한은 정확히 600이어야 합니다: {self.path} ({self.mode:o})"


class MissingAlpacaPaperCredentialsError(RuntimeError):
    __slots__ = ("names",)

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self.names = names

    @override
    def __str__(self) -> str:
        return f"Alpaca paper 자격증명이 없습니다: {', '.join(self.names)}"


class AlpacaPaperSecretEncodingError(UnicodeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 비밀 파일은 유효한 UTF-8이어야 합니다"


def load_alpaca_paper_credentials(
    path: Path = DEFAULT_ALPACA_PAPER_SECRET_PATH,
) -> AlpacaPaperCredentials:
    file_stat = path.stat()
    file_mode = stat.S_IMODE(file_stat.st_mode)
    if (
        file_mode != 0o600
        or not stat.S_ISREG(file_stat.st_mode)
        or path.is_symlink()
        or file_stat.st_uid != os.getuid()
    ):
        raise AlpacaPaperSecretFileError(path=path, mode=file_mode)
    values: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                name, separator, value = raw_line.rstrip("\n").partition("=")
                if separator:
                    values[name] = value.strip()
    except UnicodeError:
        raise AlpacaPaperSecretEncodingError from None
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
        raise MissingAlpacaPaperCredentialsError(missing)
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def create_alpaca_paper_read_client() -> httpx2.Client:
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
        base_url=ALPACA_PAPER_TRADING_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )
