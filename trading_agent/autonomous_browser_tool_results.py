from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final, assert_never

from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolInvocationError
from trading_agent.browser_social_evidence import BrowserSocialEvidence
from trading_agent.local_browser_gateway_wire import BrowserRequest
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserResponse,
    BrowserSearchResult,
)
from trading_agent.local_browser_socket import (
    InvalidLocalBrowserSocketError,
)
from trading_agent.local_browser_socket import (
    LocalBrowserSocketClient as LocalBrowserGatewayClient,
)

_MAX_RESULT_BYTES: Final = 16_384
_PAGE_RESULT_RESERVE_BYTES: Final = 512
_MAX_RETURNED_LINKS: Final = 4

type JsonValue = str | int | float | bool | None | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class InvalidBrowserToolResultError(ValueError):
    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason = "browser response failure missing"
        super().__init__(self.reason)


def canonical(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def gateway_unavailable(request_id: str) -> str:
    return canonical({"browser_receipt_id": request_id, "reason": "browser_gateway_unavailable", "status": "error"})


def failed_response(response: BrowserResponse) -> str:
    if response.failure is None:
        raise InvalidBrowserToolResultError
    return canonical(
        {"browser_receipt_id": response.request_id, "reason": response.failure.reason.value, "status": "error"}
    )


def blocked_read_failure(response: BrowserResponse, target_id: str) -> str:
    if response.failure is None:
        raise InvalidBrowserToolResultError
    return canonical(
        {
            "browser_receipt_id": response.request_id,
            "reason": response.failure.reason.value,
            "status": "blocked",
            "target_id": target_id,
        }
    )


def blocked_read_page(observation: BrowserPageObservation, request_id: str) -> str:
    return canonical(
        {
            "browser_receipt_id": request_id,
            "captured_at": observation.captured_at.isoformat(),
            "normalized_url": observation.url,
            "reason": "browser_visible_text_unavailable",
            "status": "blocked",
            "target_id": observation.target_id,
            "title": _bounded_text(observation.title, 600),
        }
    )


def page_payload(observation: BrowserPageObservation) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "captured_at": observation.captured_at.isoformat(),
        "excerpt": _bounded_text(observation.visible_text, 1_000),
        "links": (),
        "normalized_url": observation.url,
        "target_id": observation.target_id,
        "title": _bounded_text(observation.title, 600),
    }
    links: tuple[JsonValue, ...] = ()
    for link in observation.links[:_MAX_RETURNED_LINKS]:
        candidate = (*links, {"label": _bounded_text(link.label, 300), "url": link.url})
        candidate_payload = {**payload, "links": candidate}
        if len(canonical(candidate_payload).encode("ascii")) > _MAX_RESULT_BYTES - _PAGE_RESULT_RESERVE_BYTES:
            break
        links = candidate
    return {**payload, "links": links}


def evidence_excerpt(value: str) -> str:
    return _bounded_text(value, 1_000)


def gateway_request(request: BrowserRequest, gateway_socket: str, timeout_seconds: float) -> BrowserResponse | None:
    try:
        return LocalBrowserGatewayClient(Path(gateway_socket), timeout_seconds=timeout_seconds).request(request)
    except InvalidLocalBrowserSocketError:
        return None


def page_response(request: BrowserOpenRequest | BrowserFollowRequest, response: BrowserResponse | None) -> str:
    if response is None:
        return gateway_unavailable(request.request_id)
    match response.status:
        case "error":
            return failed_response(response)
        case "ok":
            observation = response.observation
            if response.action not in {BrowserAction.OPEN, BrowserAction.FOLLOW} or observation is None:
                return gateway_unavailable(request.request_id)
            return canonical({**page_payload(observation), "browser_receipt_id": response.request_id, "status": "ok"})
        case unreachable:
            assert_never(unreachable)


def required_argument(args: AutonomousToolArguments, name: str) -> str:
    value = args.root.get(name)
    if value is None:
        raise AutonomousToolInvocationError(reason="browser_tool_arguments_missing")
    return value


def link_index(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise AutonomousToolInvocationError(reason="browser_tool_arguments_invalid") from None


def evidence_limit(value: str | None) -> int:
    if value is None:
        return 20
    try:
        return int(value)
    except ValueError:
        raise AutonomousToolInvocationError(reason="browser_tool_arguments_invalid") from None


def request_id(name: str, context: AutonomousToolExecutionContext, arguments: dict[str, str]) -> str:
    payload = json.dumps(
        {"arguments": arguments, "task_id": context.task_id, "tool_name": name},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def search_payload(response: BrowserResponse) -> str:
    results: tuple[JsonValue, ...] = ()
    for item in response.search_results:
        candidate = (*results, _search_result(item))
        payload: dict[str, JsonValue] = {
            "browser_receipt_id": response.request_id,
            "search_results": candidate,
            "status": "ok",
        }
        if len(canonical(payload).encode("ascii")) > _MAX_RESULT_BYTES:
            break
        results = candidate
    return canonical({"browser_receipt_id": response.request_id, "search_results": results, "status": "ok"})


def evidence_search_payload(records: tuple[BrowserSocialEvidence, ...]) -> str:
    evidence: tuple[JsonValue, ...] = ()
    for record in records:
        candidate = (*evidence, _evidence_projection(record))
        payload: dict[str, JsonValue] = {"evidence": candidate, "status": "ok"}
        if len(canonical(payload).encode("ascii")) > _MAX_RESULT_BYTES:
            break
        evidence = candidate
    return canonical({"evidence": evidence, "status": "ok"})


def _search_result(item: BrowserSearchResult) -> dict[str, JsonValue]:
    return {"title": _bounded_text(item.title, 600), "url": item.url}


def _evidence_projection(record: BrowserSocialEvidence) -> dict[str, JsonValue]:
    return {
        "author_label": _bounded_text(record.author_label, 300),
        "browser_receipt_id": record.browser_receipt_id,
        "captured_at": record.captured_at.isoformat(),
        "evidence_id": record.evidence_id,
        "excerpt": _bounded_text(record.excerpt, 1_000),
        "normalized_url": record.normalized_url,
        "screenshot_sha256": record.screenshot_sha256,
        "source_kind": record.source_kind,
        "title": _bounded_text(record.title, 600),
    }


def _bounded_text(value: str, maximum_json_bytes: int) -> str:
    low = 0
    high = len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(value[:middle], ensure_ascii=True).encode("ascii")) <= maximum_json_bytes:
            low = middle
        else:
            high = middle - 1
    return value[:low]


__all__ = (
    "InvalidBrowserToolResultError",
    "blocked_read_failure",
    "blocked_read_page",
    "canonical",
    "evidence_excerpt",
    "evidence_limit",
    "evidence_search_payload",
    "failed_response",
    "gateway_request",
    "gateway_unavailable",
    "link_index",
    "page_payload",
    "page_response",
    "request_id",
    "required_argument",
    "search_payload",
)
