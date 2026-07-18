from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, cast

_SUMMARY_FIELDS: Final[tuple[str, str, str]] = ("changed_files", "verification", "concerns")
_MAX_SUMMARY_ITEMS: Final = 32
_MAX_SUMMARY_ITEM_LEN: Final = 240

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]

WORKER_SUMMARY_JSON_SCHEMA: Final[str] = json.dumps(
    {
        "type": "object",
        "properties": {
            "changed_files": {
                "type": "array",
                "items": {"type": "string", "maxLength": _MAX_SUMMARY_ITEM_LEN},
                "maxItems": _MAX_SUMMARY_ITEMS,
            },
            "verification": {
                "type": "array",
                "items": {"type": "string", "maxLength": _MAX_SUMMARY_ITEM_LEN},
                "maxItems": _MAX_SUMMARY_ITEMS,
            },
            "concerns": {
                "type": "array",
                "items": {"type": "string", "maxLength": _MAX_SUMMARY_ITEM_LEN},
                "maxItems": _MAX_SUMMARY_ITEMS,
            },
        },
        "required": list(_SUMMARY_FIELDS),
        "additionalProperties": False,
    },
    separators=(",", ":"),
    sort_keys=True,
)


@dataclass(frozen=True, slots=True)
class GrokWorkerSummary:
    changed_files: tuple[str, ...]
    verification: tuple[str, ...]
    concerns: tuple[str, ...]

    def as_safe_dict(self) -> dict[str, list[str]]:
        return {
            "changed_files": list(self.changed_files),
            "verification": list(self.verification),
            "concerns": list(self.concerns),
        }


@dataclass(frozen=True, slots=True)
class GrokTaskReport:
    schema_version: Literal[1]
    task_id: str
    base_commit: str
    status: Literal["planned", "completed", "worker_failed"]
    changed_paths: tuple[str, ...]
    worker_exit_code: int | None
    summary: GrokWorkerSummary | None = None

    def as_safe_dict(self) -> dict[str, int | str | list[str] | dict[str, list[str]] | None]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "base_commit": self.base_commit,
            "status": self.status,
            "changed_paths": list(self.changed_paths),
            "worker_exit_code": self.worker_exit_code,
            "summary": None if self.summary is None else self.summary.as_safe_dict(),
        }


def _is_json_tree(value: JsonValue) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_tree(cast(JsonValue, item)) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_tree(cast(JsonValue, item)) for key, item in value.items())
    return False


def _bounded_string_tuple(value: JsonValue) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    if len(value) > _MAX_SUMMARY_ITEMS:
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > _MAX_SUMMARY_ITEM_LEN:
            return None
        items.append(item)
    return tuple(items)


def _summary_from_object(payload: dict[str, JsonValue]) -> GrokWorkerSummary | None:
    if any(field not in payload for field in _SUMMARY_FIELDS):
        return None
    if any(key not in _SUMMARY_FIELDS for key in payload):
        return None
    changed_files = _bounded_string_tuple(payload["changed_files"])
    verification = _bounded_string_tuple(payload["verification"])
    concerns = _bounded_string_tuple(payload["concerns"])
    if changed_files is None or verification is None or concerns is None:
        return None
    return GrokWorkerSummary(
        changed_files=changed_files,
        verification=verification,
        concerns=concerns,
    )


def parse_worker_summary(raw_stdout: str) -> GrokWorkerSummary | None:
    """Parse only top-level structuredOutput from a Grok JSON envelope."""

    try:
        loaded = cast(JsonValue, json.loads(raw_stdout))
    except json.JSONDecodeError:
        return None
    if not _is_json_tree(loaded) or not isinstance(loaded, dict):
        return None
    # Real Grok puts the schema-validated object in structuredOutput. The text
    # field may contain multiple concatenated draft JSON objects and must not
    # be treated as the summary document.
    structured = loaded.get("structuredOutput")
    if not isinstance(structured, dict) or not _is_json_tree(structured):
        return None
    return _summary_from_object(cast(dict[str, JsonValue], structured))
