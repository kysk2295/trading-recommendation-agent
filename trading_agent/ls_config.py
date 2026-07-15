from __future__ import annotations

import os
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

LS_REST_BASE_URL: Final = "https://openapi.ls-sec.co.kr:8080"
DEFAULT_LS_SECRET_PATH: Final = Path.home() / ".config/trading-agent/ls.env"
MAX_LS_SECRET_FILE_BYTES: Final = 1_024
_EXPECTED_SETTINGS: Final = frozenset(("LS_APP_KEY", "LS_APP_SECRET"))


@dataclass(frozen=True, slots=True)
class LsCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_secret_value(self.app_key) or not _valid_secret_value(
            self.app_secret
        ):
            raise InvalidLsCredentialsError


class LsSecretFileError(PermissionError):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @override
    def __str__(self) -> str:
        return f"LS 비밀 파일은 현재 사용자 소유 mode 600 regular file이어야 합니다: {self.path}"


class LsSecretEncodingError(UnicodeError):
    @override
    def __str__(self) -> str:
        return "LS 비밀 파일은 유효한 UTF-8이어야 합니다"


class InvalidLsCredentialsError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS 비밀 파일에는 유효한 App Key와 App Secret만 필요합니다"


def load_ls_credentials(path: Path = DEFAULT_LS_SECRET_PATH) -> LsCredentials:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError:
        raise LsSecretFileError(path) from None
    try:
        file_stat = os.fstat(descriptor)
        if (
            stat.S_IMODE(file_stat.st_mode) != 0o600
            or not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.getuid()
        ):
            raise LsSecretFileError(path)
        payload = bytearray()
        while len(payload) <= MAX_LS_SECRET_FILE_BYTES:
            chunk = os.read(
                descriptor,
                MAX_LS_SECRET_FILE_BYTES + 1 - len(payload),
            )
            if not chunk:
                break
            payload.extend(chunk)
    except LsSecretFileError:
        raise
    except OSError:
        raise LsSecretFileError(path) from None
    finally:
        os.close(descriptor)
    if len(payload) > MAX_LS_SECRET_FILE_BYTES:
        raise InvalidLsCredentialsError
    try:
        text = bytes(payload).decode("utf-8")
    except UnicodeError:
        raise LsSecretEncodingError from None

    lines = text.splitlines()
    values: dict[str, str] = {}
    if len(lines) != 2:
        raise InvalidLsCredentialsError
    for line in lines:
        name, separator, value = line.partition("=")
        if (
            separator != "="
            or name not in _EXPECTED_SETTINGS
            or name in values
            or not _valid_secret_value(value)
        ):
            raise InvalidLsCredentialsError
        values[name] = value
    if values.keys() != _EXPECTED_SETTINGS:
        raise InvalidLsCredentialsError
    return LsCredentials(
        app_key=values["LS_APP_KEY"],
        app_secret=values["LS_APP_SECRET"],
    )


def create_ls_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=4,
        max_keepalive_connections=2,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=1,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=LS_REST_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        follow_redirects=False,
        trust_env=False,
    )


def _valid_secret_value(value: str) -> bool:
    return (
        20 <= len(value) <= 256
        and value.isascii()
        and all(33 <= ord(character) <= 126 for character in value)
    )
