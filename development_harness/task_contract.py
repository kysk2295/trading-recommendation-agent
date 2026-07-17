from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_COMMAND_PATTERN = re.compile(r"^[A-Za-z0-9_./:=+@%,-]+(?: [A-Za-z0-9_./:=+@%,-]+)*$")
_ALLOWED_COMMANDS = frozenset({"pytest", "ruff", "basedpyright", "python"})
_TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_PROTECTED_ROOTS = frozenset({".git", ".grok", ".hermes"})
_SECRET_PATH_PARTS = frozenset({"credentials", "credential", "secrets", "secret", "id_rsa"})
_SECRET_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx")
_MODEL_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "base_commit",
        "objective",
        "allowed_paths",
        "required_commands",
        "manual_qa_commands",
        "expected_summary_fields",
        "max_turns",
    }
)


def _is_safe_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    for part in path.parts:
        lower = part.lower()
        if lower in _PROTECTED_ROOTS or lower in _SECRET_PATH_PARTS:
            return False
        if lower == ".env" or lower.startswith(".env."):
            return False
        if "token" in lower or "secret" in lower or lower.endswith(_SECRET_SUFFIXES):
            return False
    return True


def _is_safe_command(value: str) -> bool:
    if not _COMMAND_PATTERN.fullmatch(value):
        return False
    parts = value.split()
    if len(parts) < 3 or parts[:2] != ["uv", "run"] or parts[2] not in _ALLOWED_COMMANDS:
        return False
    return parts[2] == "basedpyright" or len(parts) > 3


class InvalidGrokTaskContractError(ValueError):
    """A sanitized task-contract validation failure."""

    def __init__(self) -> None:
        super().__init__("invalid Grok task contract")


class GrokTaskContract(BaseModel):
    """The immutable, side-effect-bounded input to one development worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    task_id: Annotated[str, Field(min_length=3, max_length=80)]
    base_commit: str
    objective: Annotated[str, Field(min_length=1, max_length=800)]
    allowed_paths: tuple[str, ...]
    required_commands: tuple[str, ...]
    manual_qa_commands: tuple[str, ...]
    expected_summary_fields: tuple[str, ...]
    max_turns: Annotated[int, Field(ge=1, le=24)] = 12

    _summary_field_pattern: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        try:
            return super().model_validate(
                obj,
                strict=strict,
                extra=extra,
                from_attributes=from_attributes,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        try:
            return super().model_validate_json(
                json_data,
                strict=strict,
                extra=extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError:
            raise InvalidGrokTaskContractError() from None

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_or_non_mapping_input(cls, value: Any) -> Any:
        if not isinstance(value, dict) or set(value).difference(_MODEL_FIELDS):
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        if not _TASK_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("base_commit")
    @classmethod
    def _validate_base_commit(cls, value: str) -> str:
        if not _COMMIT_PATTERN.fullmatch(value):
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("allowed_paths")
    @classmethod
    def _validate_allowed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)) or any(not _is_safe_path(path) for path in value):
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("required_commands", "manual_qa_commands")
    @classmethod
    def _validate_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not _is_safe_command(command) for command in value):
            raise ValueError("invalid Grok task contract")
        return value

    @field_validator("expected_summary_fields")
    @classmethod
    def _validate_expected_summary_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("invalid Grok task contract")
        if any(not cls._summary_field_pattern.fullmatch(field) for field in value):
            raise ValueError("invalid Grok task contract")
        return value

    @model_validator(mode="after")
    def _reject_empty_values(self) -> GrokTaskContract:
        if not self.objective or not self.required_commands or not self.manual_qa_commands:
            raise ValueError("invalid Grok task contract")
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Keep Pydantic's convenience API from bypassing contract validation."""

        _ = deep
        payload = self.model_dump(mode="python")
        if update is not None:
            payload.update(update)
        return type(self).model_validate(payload)
