from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_immutable_file import publish_private_immutable_text


class RejectedTriggerReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    raw_trigger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["rejected"] = "rejected"
    reason: Literal["invalid_trigger_schema", "invalid_trigger_source"]
    occurred_at: AwareDatetime


class RejectedTriggerStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        descriptor = open_private_parent(root, create=True)
        try:
            require_private_directory(descriptor)
        finally:
            os.close(descriptor)

    def append_path(
        self,
        path: Path,
        reason: Literal["invalid_trigger_schema", "invalid_trigger_source"],
        *,
        raw: str | None = None,
    ) -> bool:
        try:
            occurred_at = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
        except OSError:
            occurred_at = dt.datetime.fromtimestamp(0, tz=dt.UTC)
        source = str(path.resolve(strict=False)) if raw is None else raw
        digest = hashlib.sha256(source.encode()).hexdigest()
        receipt = RejectedTriggerReceiptV1(
            raw_trigger_sha256=digest,
            reason=reason,
            occurred_at=occurred_at,
        )
        return publish_private_immutable_text(self._root / f"{digest}.{reason}.json", receipt.model_dump_json())


__all__ = ("RejectedTriggerReceiptV1", "RejectedTriggerStore")
