from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)


class InvalidHermesCredentialStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Hermes credential store is invalid"


def materialize_hermes_auth(source: Path, hermes_home: Path) -> Path:
    target = hermes_home / "auth.json"
    try:
        payload = read_private_text_query_only(source)
        _ = publish_private_immutable_text(target, payload)
    except (
        InvalidPrivateImmutableFileError,
        InvalidPrivateQueryFileError,
    ) as error:
        raise InvalidHermesCredentialStoreError from error
    return target


__all__ = ("InvalidHermesCredentialStoreError", "materialize_hermes_auth")
