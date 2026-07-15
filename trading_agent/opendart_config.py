from __future__ import annotations

import os
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

OPENDART_BASE_URL: Final = "https://opendart.fss.or.kr"
DEFAULT_OPENDART_SECRET_PATH: Final = (
    Path.home() / ".config/trading-agent/opendart.env"
)


@dataclass(frozen=True, slots=True)
class OpenDartCredentials:
    api_key: str = field(repr=False)


class OpenDartSecretFileError(PermissionError):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @override
    def __str__(self) -> str:
        return f"OpenDART 비밀 파일은 소유자 mode 600 regular file이어야 합니다: {self.path}"


class OpenDartSecretEncodingError(UnicodeError):
    @override
    def __str__(self) -> str:
        return "OpenDART 비밀 파일은 유효한 UTF-8이어야 합니다"


class InvalidOpenDartCredentialsError(ValueError):
    @override
    def __str__(self) -> str:
        return "OpenDART 비밀 파일에는 유효한 API 키 설정 하나가 필요합니다"


def load_opendart_credentials(
    path: Path = DEFAULT_OPENDART_SECRET_PATH,
) -> OpenDartCredentials:
    try:
        file_stat = path.lstat()
    except OSError:
        raise OpenDartSecretFileError(path) from None
    if (
        stat.S_IMODE(file_stat.st_mode) != 0o600
        or not stat.S_ISREG(file_stat.st_mode)
        or path.is_symlink()
        or file_stat.st_uid != os.getuid()
    ):
        raise OpenDartSecretFileError(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeError:
        raise OpenDartSecretEncodingError from None
    except OSError:
        raise OpenDartSecretFileError(path) from None
    lines = text.splitlines()
    if len(lines) != 1:
        raise InvalidOpenDartCredentialsError
    name, separator, value = lines[0].partition("=")
    if (
        separator != "="
        or name != "OPENDART_API_KEY"
        or len(value) != 40
        or not value.isascii()
        or any(not 33 <= ord(character) <= 126 for character in value)
    ):
        raise InvalidOpenDartCredentialsError
    return OpenDartCredentials(value)


def create_opendart_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=4,
        max_keepalive_connections=2,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=OPENDART_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )
