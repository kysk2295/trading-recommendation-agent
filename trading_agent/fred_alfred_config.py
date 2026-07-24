from __future__ import annotations

import os
import re
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

FRED_BASE_URL: Final = "https://api.stlouisfed.org"
DEFAULT_FRED_SECRET_PATH: Final = Path.home() / ".config/trading-agent/fred.env"
MAX_FRED_SECRET_FILE_BYTES: Final = 128
_API_KEY = re.compile(r"^[a-z0-9]{32}$")


class FredCredentialFileError(PermissionError):
    @override
    def __str__(self) -> str:
        return "FRED credential file must be a current-owner mode 600 regular file"


class InvalidFredCredentialsError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED credential file must contain one valid FRED_API_KEY setting"


@dataclass(frozen=True, slots=True)
class FredCredentials:
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if _API_KEY.fullmatch(self.api_key) is None:
            raise InvalidFredCredentialsError


def load_fred_credentials(
    path: Path = DEFAULT_FRED_SECRET_PATH,
) -> FredCredentials:
    if not path.is_absolute():
        raise FredCredentialFileError
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError:
        raise FredCredentialFileError from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise FredCredentialFileError
        payload = os.read(descriptor, MAX_FRED_SECRET_FILE_BYTES + 1)
    except FredCredentialFileError:
        raise
    except OSError:
        raise FredCredentialFileError from None
    finally:
        os.close(descriptor)
    if len(payload) > MAX_FRED_SECRET_FILE_BYTES:
        raise InvalidFredCredentialsError
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise InvalidFredCredentialsError from None
    lines = text.splitlines()
    if len(lines) != 1:
        raise InvalidFredCredentialsError
    name, separator, value = lines[0].partition("=")
    if name != "FRED_API_KEY" or separator != "=":
        raise InvalidFredCredentialsError
    return FredCredentials(value)


def create_fred_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=2,
        max_keepalive_connections=1,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=FRED_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
        trust_env=False,
    )


__all__ = (
    "DEFAULT_FRED_SECRET_PATH",
    "FRED_BASE_URL",
    "FredCredentialFileError",
    "FredCredentials",
    "InvalidFredCredentialsError",
    "create_fred_http_client",
    "load_fred_credentials",
)
