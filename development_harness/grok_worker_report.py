from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, cast

_SUMMARY_FIELDS: Final[tuple[str, str, str]] = ("changed_files", "verification", "concerns")
_MAX_SUMMARY_ITEMS: Final = 32
_MAX_SUMMARY_ITEM_LEN: Final = 240
_MAX_JSON_DEPTH: Final = 8
ALLOWED_CONCERNS: Final[frozenset[str]] = frozenset(
    {
        "timeout_risk",
        "scope_pressure",
        "test_gap",
        "docs_gap",
        "verification_gap",
        "residual_risk",
    }
)

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
                "items": {
                    "type": "string",
                    "enum": sorted(ALLOWED_CONCERNS),
                    "maxLength": _MAX_SUMMARY_ITEM_LEN,
                },
                "maxItems": _MAX_SUMMARY_ITEMS,
                "uniqueItems": True,
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


def _json_depth(value: JsonValue, depth: int = 1) -> int:
    if depth > _MAX_JSON_DEPTH:
        return depth
    if isinstance(value, list):
        if not value:
            return depth
        return max(_json_depth(cast(JsonValue, item), depth + 1) for item in value)
    if isinstance(value, dict):
        if not value:
            return depth
        return max(_json_depth(cast(JsonValue, item), depth + 1) for item in value.values())
    return depth


def _is_json_tree(value: JsonValue, *, max_depth: int) -> bool:
    if _json_depth(value) > max_depth:
        return False
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_tree(cast(JsonValue, item), max_depth=max_depth) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_tree(cast(JsonValue, item), max_depth=max_depth)
            for key, item in value.items()
        )
    return False


def _bounded_string_tuple(value: JsonValue) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > _MAX_SUMMARY_ITEMS:
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > _MAX_SUMMARY_ITEM_LEN:
            return None
        items.append(item)
    return tuple(items)


def _summary_from_object(
    payload: dict[str, JsonValue],
    *,
    allowed_paths: frozenset[str] | None,
    required_verification: frozenset[str] | None,
) -> GrokWorkerSummary | None:
    if any(field not in payload for field in _SUMMARY_FIELDS):
        return None
    if any(key not in _SUMMARY_FIELDS for key in payload):
        return None
    changed_files = _bounded_string_tuple(payload["changed_files"])
    verification = _bounded_string_tuple(payload["verification"])
    concerns = _bounded_string_tuple(payload["concerns"])
    if changed_files is None or verification is None or concerns is None:
        return None
    if allowed_paths is not None and any(path not in allowed_paths for path in changed_files):
        return None
    if required_verification is not None:
        if len(verification) != len(set(verification)):
            return None
        if set(verification) != required_verification:
            return None
    if len(concerns) != len(set(concerns)):
        return None
    if any(item not in ALLOWED_CONCERNS for item in concerns):
        return None
    return GrokWorkerSummary(
        changed_files=changed_files,
        verification=verification,
        concerns=concerns,
    )


def parse_worker_summary(
    raw_stdout: str,
    *,
    allowed_paths: frozenset[str] | None = None,
    required_verification: frozenset[str] | None = None,
    max_json_depth: int = _MAX_JSON_DEPTH,
) -> GrokWorkerSummary | None:
    """Parse only top-level structuredOutput from a Grok JSON envelope."""

    try:
        loaded = cast(JsonValue, json.loads(raw_stdout))
    except (json.JSONDecodeError, RecursionError):
        return None
    try:
        if not _is_json_tree(loaded, max_depth=max_json_depth) or not isinstance(loaded, dict):
            return None
        structured = loaded.get("structuredOutput")
        if not isinstance(structured, dict) or not _is_json_tree(
            cast(JsonValue, structured), max_depth=max_json_depth
        ):
            return None
    except RecursionError:
        return None
    return _summary_from_object(
        cast(dict[str, JsonValue], structured),
        allowed_paths=allowed_paths,
        required_verification=required_verification,
    )
