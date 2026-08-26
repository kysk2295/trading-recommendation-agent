from __future__ import annotations

from pathlib import Path
from typing import assert_never

from trading_agent.autonomous_browser_tool_results import (
    blocked_read_failure,
    blocked_read_page,
    canonical,
    evidence_excerpt,
    evidence_limit,
    evidence_search_payload,
    failed_response,
    gateway_request,
    gateway_unavailable,
    link_index,
    page_payload,
    page_response,
    request_id,
    required_argument,
    search_payload,
)
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolInvocationError
from trading_agent.browser_social_evidence import (
    BrowserSocialEvidenceCapture,
    browser_social_evidence,
    browser_source_identity_sha256,
)
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserCaptureRequest,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserReadRequest,
    BrowserResponse,
    BrowserSearchRequest,
    BrowserStatusRequest,
)


def browser_status_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del args, evidence_database
    request = BrowserStatusRequest(request_id=request_id("browser.status", context, {}))
    response = gateway_request(request, gateway_socket, timeout_seconds)
    if response is None:
        return gateway_unavailable(request.request_id)
    match response.status:
        case "error":
            return _failed(response)
        case "ok":
            payload = response.status_payload
            if response.action is not BrowserAction.STATUS or payload is None:
                return gateway_unavailable(request.request_id)
            return canonical(
                {
                    "active_page_count": payload.active_page_count,
                    "browser_receipt_id": response.request_id,
                    "ready": payload.ready,
                    "status": "ok",
                }
            )
        case unreachable:
            assert_never(unreachable)


def browser_search_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del evidence_database
    query = required_argument(args, "query")
    request = BrowserSearchRequest(request_id=request_id("browser.search", context, {"query": query}), query=query)
    response = gateway_request(request, gateway_socket, timeout_seconds)
    if response is None:
        return gateway_unavailable(request.request_id)
    match response.status:
        case "error":
            return _failed(response)
        case "ok":
            if response.action is not BrowserAction.SEARCH:
                return gateway_unavailable(request.request_id)
            return search_payload(response)
        case unreachable:
            assert_never(unreachable)


def browser_open_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del evidence_database
    url = required_argument(args, "url")
    request = BrowserOpenRequest(request_id=request_id("browser.open", context, {"url": url}), url=url)
    return page_response(request, gateway_request(request, gateway_socket, timeout_seconds))


def browser_read_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    target_id = required_argument(args, "target_id")
    request = BrowserReadRequest(
        request_id=request_id("browser.read", context, {"target_id": target_id}), target_id=target_id
    )
    response = gateway_request(request, gateway_socket, timeout_seconds)
    if response is None:
        return gateway_unavailable(request.request_id)
    match response.status:
        case "error":
            return _blocked_failure(response, target_id)
        case "ok":
            observation = response.observation
            if response.action is not BrowserAction.READ or observation is None:
                return gateway_unavailable(request.request_id)
            excerpt = evidence_excerpt(observation.visible_text)
            if not excerpt:
                return blocked_read_page(observation, response.request_id)
            evidence = browser_social_evidence(
                BrowserSocialEvidenceCapture(
                    browser_receipt_id=response.request_id,
                    normalized_url=observation.url,
                    source_kind="web",
                    source_identity_sha256=browser_source_identity_sha256(observation.url),
                    title=observation.title,
                    excerpt=excerpt,
                    first_observed_at=observation.captured_at,
                    captured_at=observation.captured_at,
                )
            )
            try:
                BrowserSocialEvidenceStore(Path(evidence_database)).append(evidence)
            except RuntimeError:
                raise AutonomousToolInvocationError(reason="browser_evidence_append_failed") from None
            return canonical(
                {
                    **page_payload(observation),
                    "browser_receipt_id": response.request_id,
                    "evidence_id": evidence.evidence_id,
                    "screenshot_sha256": evidence.screenshot_sha256,
                    "status": "ok",
                }
            )
        case unreachable:
            assert_never(unreachable)


def browser_follow_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del evidence_database
    target_id = required_argument(args, "target_id")
    index = link_index(required_argument(args, "link_index"))
    request = BrowserFollowRequest(
        request_id=request_id("browser.follow", context, {"link_index": str(index), "target_id": target_id}),
        target_id=target_id,
        link_index=index,
    )
    return page_response(request, gateway_request(request, gateway_socket, timeout_seconds))


def browser_capture_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del evidence_database
    target_id = required_argument(args, "target_id")
    request = BrowserCaptureRequest(
        request_id=request_id("browser.capture", context, {"target_id": target_id}), target_id=target_id
    )
    response = gateway_request(request, gateway_socket, timeout_seconds)
    if response is None:
        return gateway_unavailable(request.request_id)
    match response.status:
        case "error":
            return _failed(response)
        case "ok":
            screenshot = response.screenshot
            if response.action is not BrowserAction.CAPTURE or screenshot is None:
                return gateway_unavailable(request.request_id)
            return canonical(
                {
                    "browser_receipt_id": response.request_id,
                    "captured_at": screenshot.captured_at.isoformat(),
                    "screenshot_sha256": screenshot.sha256,
                    "status": "ok",
                }
            )
        case unreachable:
            assert_never(unreachable)


def social_evidence_search_tool(
    args: AutonomousToolArguments,
    _context: AutonomousToolExecutionContext,
    *,
    gateway_socket: str,
    evidence_database: str,
    timeout_seconds: float,
) -> str:
    del gateway_socket, timeout_seconds
    try:
        records = BrowserSocialEvidenceStore(Path(evidence_database)).search(
            required_argument(args, "query"), limit=evidence_limit(args.root.get("limit"))
        )
    except RuntimeError:
        raise AutonomousToolInvocationError(reason="browser_evidence_search_failed") from None
    return evidence_search_payload(records)


def _failed(response: BrowserResponse) -> str:
    try:
        return failed_response(response)
    except ValueError:
        raise AutonomousToolInvocationError(reason="browser_response_invalid") from None


def _blocked_failure(response: BrowserResponse, target_id: str) -> str:
    try:
        return blocked_read_failure(response, target_id)
    except ValueError:
        raise AutonomousToolInvocationError(reason="browser_response_invalid") from None
