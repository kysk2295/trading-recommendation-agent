from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

_SESSION_PATTERN: Final = r"^[A-Za-z0-9_-]{8,128}$"


class InvalidHermesSessionBindingError(RuntimeError):
    pass


class _Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    agent_family_id: AgentFamilyId
    session_id: str = Field(pattern=_SESSION_PATTERN)


class HermesSessionBindingStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def session_for(self, family_id: AgentFamilyId) -> str | None:
        path = self._path(family_id)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            binding = _Binding.model_validate_json(read_private_text_query_only(path))
        except (InvalidPrivateQueryFileError, ValidationError) as error:
            raise InvalidHermesSessionBindingError("invalid_hermes_session_binding") from error
        if binding.agent_family_id != family_id:
            raise InvalidHermesSessionBindingError("hermes_session_family_mismatch")
        return binding.session_id

    def capture(self, family_id: AgentFamilyId, session_id: str) -> None:
        binding = _Binding(agent_family_id=family_id, session_id=session_id)
        try:
            _ = publish_private_immutable_text(
                self._path(family_id),
                json.dumps(binding.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            )
        except InvalidPrivateImmutableFileError as error:
            raise InvalidHermesSessionBindingError("hermes_session_capture_failed") from error

    def reset(self, family_id: AgentFamilyId) -> None:
        path = self._path(family_id)
        if not path.exists() and not path.is_symlink():
            return
        _ = self.session_for(family_id)
        try:
            path.unlink()
        except OSError as error:
            raise InvalidHermesSessionBindingError("hermes_session_reset_failed") from error

    def _path(self, family_id: AgentFamilyId) -> Path:
        return self._root / f"{family_id}.json"


__all__ = ("HermesSessionBindingStore", "InvalidHermesSessionBindingError")
