from __future__ import annotations

import os
import re
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

import httpx2

SEC_EDGAR_BASE_URL: Final = "https://data.sec.gov"
DEFAULT_SEC_USER_AGENT_PATH: Final = Path.home() / ".config/trading-agent/sec.env"
MAX_SEC_USER_AGENT_FILE_BYTES: Final = 1_024
_USER_AGENT = re.compile(r"^[!-~]+(?: [!-~]+)+$")
_CONTACT = re.compile(r"(?<![^ <])[^ @<>]+@[^ @<>]+\.[A-Za-z]{2,}(?![^ >])")


class SecUserAgentFileError(PermissionError):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @override
    def __str__(self) -> str:
        return f"SEC User-Agent file must be a current-owner mode 600 regular file: {self.path}"


class InvalidSecUserAgentError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC_USER_AGENT must declare an application and contact email"


@dataclass(frozen=True, slots=True)
class SecUserAgent:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not 10 <= len(self.value) <= 256
            or not self.value.isascii()
            or _USER_AGENT.fullmatch(self.value) is None
            or _CONTACT.search(self.value) is None
        ):
            raise InvalidSecUserAgentError


def load_sec_user_agent(path: Path = DEFAULT_SEC_USER_AGENT_PATH) -> SecUserAgent:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError:
        raise SecUserAgentFileError(path) from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise SecUserAgentFileError(path)
        payload = bytearray()
        while len(payload) <= MAX_SEC_USER_AGENT_FILE_BYTES:
            chunk = os.read(descriptor, MAX_SEC_USER_AGENT_FILE_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
    except SecUserAgentFileError:
        raise
    except OSError:
        raise SecUserAgentFileError(path) from None
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SEC_USER_AGENT_FILE_BYTES:
        raise InvalidSecUserAgentError
    try:
        text = bytes(payload).decode("utf-8")
    except UnicodeError:
        raise InvalidSecUserAgentError from None
    lines = text.splitlines()
    if len(lines) != 1:
        raise InvalidSecUserAgentError
    name, separator, value = lines[0].partition("=")
    if name != "SEC_USER_AGENT" or separator != "=":
        raise InvalidSecUserAgentError
    return SecUserAgent(value)


def create_sec_edgar_http_client() -> httpx2.Client:
    limits = httpx2.Limits(max_connections=2, max_keepalive_connections=1, keepalive_expiry=30.0)
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=SEC_EDGAR_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )
