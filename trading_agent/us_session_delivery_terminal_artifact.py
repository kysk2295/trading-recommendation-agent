from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_session_delivery_terminal import (
    InvalidUsSessionDeliveryTerminalError,
    UsSessionDeliveryTerminalArtifact,
)


def write_us_session_delivery_terminal(
    destination: Path,
    artifact: UsSessionDeliveryTerminalArtifact,
) -> None:
    validated = UsSessionDeliveryTerminalArtifact.model_validate(
        artifact.model_dump(mode="python")
    )
    write_private_stable_report(
        destination,
        validated.model_dump_json(indent=2) + "\n",
    )


def read_us_session_delivery_terminal(
    source: Path,
) -> UsSessionDeliveryTerminalArtifact:
    try:
        return UsSessionDeliveryTerminalArtifact.model_validate_json(
            read_private_text_query_only(source)
        )
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidUsSessionDeliveryTerminalError from None
