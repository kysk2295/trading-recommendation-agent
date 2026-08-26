from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserResponse,
    BrowserSearchResult,
    BrowserStatusPayload,
    BrowserVisibleLink,
)


def test_browser_action_is_closed_read_only_set() -> None:
    assert {action.value for action in BrowserAction} == {
        "status",
        "search",
        "open",
        "read",
        "follow",
        "capture",
    }


def test_page_observation_rejects_extra_raw_html_and_is_utc() -> None:
    with pytest.raises(ValidationError):
        BrowserPageObservation.model_validate(
            {
                "target_id": "target-1",
                "url": "https://example.com/story",
                "title": "Story",
                "visible_text": "bounded",
                "links": [],
                "captured_at": datetime.now(UTC),
                "raw_html": "<html>forbidden</html>",
            }
        )


def test_observation_normalizes_aware_timestamp_to_utc() -> None:
    observation = BrowserPageObservation(
        target_id="target-1",
        url="https://example.com/story",
        captured_at=datetime(2026, 8, 26, 21, 0, tzinfo=timezone(timedelta(hours=9))),
    )
    assert observation.captured_at.tzinfo == UTC
    assert observation.captured_at.hour == 12


def test_request_id_query_target_and_link_bounds_are_strict() -> None:
    with pytest.raises(ValidationError):
        BrowserOpenRequest(request_id="A" * 64, url="https://example.com")
    with pytest.raises(ValidationError):
        BrowserOpenRequest(request_id="a" * 63, url="https://example.com")
    with pytest.raises(ValidationError):
        BrowserFollowRequest(request_id="a" * 64, target_id="target", link_index=100)
    with pytest.raises(ValidationError):
        BrowserVisibleLink(label="x" * 201, url="https://example.com")


def test_observation_limits_visible_text_links_and_link_url() -> None:
    with pytest.raises(ValidationError):
        BrowserPageObservation(
            target_id="target",
            url="https://example.com",
            visible_text="x" * 12_001,
            captured_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        BrowserPageObservation(
            target_id="target",
            url="https://example.com",
            links=tuple(BrowserVisibleLink(label="x", url="https://example.com") for _ in range(41)),
            captured_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        BrowserVisibleLink(label="x", url="https://example.com/" + "x" * 2048)


def test_follow_request_is_frozen_and_extra_fields_forbidden() -> None:
    request = BrowserFollowRequest(request_id="a" * 64, target_id="target", link_index=0)
    with pytest.raises(ValidationError):
        request.link_index = 1
    with pytest.raises(ValidationError):
        BrowserFollowRequest.model_validate(
            {"request_id": "a" * 64, "target_id": "target", "link_index": 0, "extra": "x"}
        )


def test_response_rejects_canonical_json_over_16_kib() -> None:
    results = tuple(BrowserSearchResult(title="result", url="https://example.com/" + "x" * 2_000) for _ in range(40))
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate(
            {"request_id": "a" * 64, "action": BrowserAction.SEARCH, "search_results": results}
        )


def test_response_error_requires_failure_and_forbids_success_payload() -> None:
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate({"request_id": "a" * 64, "action": BrowserAction.OPEN, "status": "error"})
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate(
            {
                "request_id": "a" * 64,
                "action": BrowserAction.OPEN,
                "status": "error",
                "failure": {"reason": "browser_navigation_blocked"},
                "observation": {
                    "target_id": "target",
                    "url": "https://example.com",
                    "captured_at": datetime.now(UTC),
                },
            }
        )


def test_response_ok_requires_action_specific_payload_and_forbids_failure() -> None:
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate({"request_id": "a" * 64, "action": BrowserAction.OPEN, "status": "ok"})
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate(
            {
                "request_id": "a" * 64,
                "action": BrowserAction.OPEN,
                "status": "ok",
                "failure": {"reason": "browser_navigation_blocked"},
                "observation": {
                    "target_id": "target",
                    "url": "https://example.com",
                    "captured_at": datetime.now(UTC),
                },
            }
        )


def test_response_rejects_action_payload_mismatch() -> None:
    with pytest.raises(ValidationError):
        BrowserResponse.model_validate(
            {
                "request_id": "a" * 64,
                "action": BrowserAction.STATUS,
                "status": "ok",
                "search_results": (),
            }
        )


def test_status_payload_preserves_bounded_active_page_count() -> None:
    payload = BrowserStatusPayload(ready=True, active_page_count=7)
    assert payload.model_dump_json() == '{"ready":true,"active_page_count":7}'
    for invalid_count in (-1, 101):
        with pytest.raises(ValidationError):
            BrowserStatusPayload(ready=True, active_page_count=invalid_count)
