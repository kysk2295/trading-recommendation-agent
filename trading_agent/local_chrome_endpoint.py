from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, NewType

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.local_browser_private_fs import PrivateBrowserFile

ChromeDebugPort = NewType("ChromeDebugPort", int)
_PORT_FILE_TEXT = re.compile(r"([1-9][0-9]{0,4})\n(/devtools/browser/[A-Za-z0-9_-]{1,128})\n?\Z")


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class InvalidLocalChromeEndpointInvariantError(ValueError):
    """Carry an endpoint invariant failure while permitting traceback attachment."""

    reason: str = "local_chrome_endpoint_ownership_invalid"

    def __str__(self) -> str:
        return self.reason


class LocalChromeEndpoint(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    port: ChromeDebugPort
    browser_path: str
    browser_websocket_url: str
    ownership: Literal["owned", "attached"]
    process_id: int | None

    @model_validator(mode="after")
    def require_honest_ownership(self) -> LocalChromeEndpoint:
        if (self.ownership == "owned") != (self.process_id is not None):
            raise InvalidLocalChromeEndpointInvariantError()
        return self


@dataclass(frozen=True, slots=True)
class PortRecord:
    file: PrivateBrowserFile
    port: ChromeDebugPort
    browser_path: str


def parse_port_file(file: PrivateBrowserFile | None) -> PortRecord | None:
    if file is None:
        return None
    try:
        match = _PORT_FILE_TEXT.fullmatch(file.payload.decode("utf-8"))
    except UnicodeDecodeError:
        return None
    if match is None or int(match.group(1)) > 65535:
        return None
    return PortRecord(file, ChromeDebugPort(int(match.group(1))), match.group(2))


def local_chrome_endpoint(
    record: PortRecord, ownership: Literal["owned", "attached"], process_id: int | None
) -> LocalChromeEndpoint:
    return LocalChromeEndpoint(
        port=record.port,
        browser_path=record.browser_path,
        browser_websocket_url=f"ws://127.0.0.1:{record.port}{record.browser_path}",
        ownership=ownership,
        process_id=process_id,
    )
