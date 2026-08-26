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
    BrowserVisibleLink,
    InvalidLocalBrowserProtocolError,
    require_public_https_url,
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


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com",
        "https://user:password@example.com/private",
        "https://127.0.0.1/admin",
        "https://[::1]/admin",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,secret",
        "chrome://settings",
        "https://example.com:8443/private",
        "https://example.com:bad/private",
        "https:///missing-host",
        "https://239.0.0.1/multicast",
        "https://169.254.1.1/link-local",
        "https://[fe80::1]/link-local",
        "https://0x7f000001/admin",
        "https://0x7f.0.0.1/admin",
        "https://0177.0.0.1/admin",
        "https://127.1/admin",
        "https://2130706433/admin",
        "https://-invalid.example",
        "https://invalid-.example",
    ),
)
def test_navigation_rejects_non_public_https_urls(url: str) -> None:
    with pytest.raises(InvalidLocalBrowserProtocolError) as error:
        BrowserOpenRequest(request_id="a" * 64, url=url)
    assert error.value.reason == "browser_url_not_public_https"


def test_public_https_url_is_normalized_without_fragment() -> None:
    assert require_public_https_url("HTTPS://Example.COM/story?q=one#fragment") == "https://example.com/story?q=one"
    assert require_public_https_url("https://Example.COM:443/story") == "https://example.com/story"


def test_public_https_url_rejects_credential_syntax_in_fragment() -> None:
    with pytest.raises(InvalidLocalBrowserProtocolError) as error:
        require_public_https_url("https://example.com/#user:password")
    assert error.value.reason == "browser_url_not_public_https"
    with pytest.raises(InvalidLocalBrowserProtocolError):
        require_public_https_url("https://example.com/#user:password@example.net")
    with pytest.raises(InvalidLocalBrowserProtocolError):
        require_public_https_url("https://example.com/#https://user:password@example.net")


def test_public_https_url_strips_ordinary_colon_fragments() -> None:
    assert require_public_https_url("https://example.com/#section:2") == "https://example.com/"
    assert require_public_https_url("https://example.com/#t=00:12") == "https://example.com/"
    assert require_public_https_url("https://example.com/#https://example.net/story") == "https://example.com/"


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
        BrowserResponse.model_validate(
            {"request_id": "a" * 64, "action": BrowserAction.OPEN, "status": "error"}
        )
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
        BrowserResponse.model_validate(
            {"request_id": "a" * 64, "action": BrowserAction.OPEN, "status": "ok"}
        )
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
