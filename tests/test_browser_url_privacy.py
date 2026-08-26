from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trading_agent.local_browser_protocol import (
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserVisibleLink,
    InvalidLocalBrowserProtocolError,
    require_public_https_url,
)


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
        "https://100.64.0.1/admin",
        "https://[::ffff:100.64.0.1]/admin",
        "https://localhost./admin",
        "https://service.localhost/admin",
        "https://bücher.example/admin",
        "https://-invalid.example",
        "https://invalid-.example",
    ),
)
def test_navigation_validation_redacts_non_public_https_urls(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        BrowserOpenRequest(request_id="a" * 64, url=url)
    assert error.value.errors(include_input=False)[0]["type"] == "value_error"
    assert url not in str(error.value)


def test_public_https_url_is_normalized_without_fragment() -> None:
    assert require_public_https_url("HTTPS://Example.COM/story?q=one#fragment") == "https://example.com/story?q=one"
    assert require_public_https_url("https://Example.COM:443/story") == "https://example.com/story"
    assert require_public_https_url("https://xn--bcher-kva.example/story") == "https://xn--bcher-kva.example/story"


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


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/?authorization=withheld",
        "https://example.com/?ACCESS%2DTOKEN=withheld",
        "https://example.com/?refresh_key=withheld",
        "https://example.com/?api%5fsecret=withheld",
        "https://example.com/?Session-ID=withheld",
        "https://example.com/?client%20secret=withheld",
        "https://example.com/?account%2Fnumber=withheld",
        "https://example.com/?token=withheld",
        "https://example.com/?auth%2Dtoken=withheld",
        "https://example.com/?SeCrEt=withheld",
        "https://example.com/?auth.secret=withheld",
        "https://example.com/?jwt=withheld",
        "https://example.com/?token%3Dwithheld",
        "https://example.com/?auth.secret%3Dwithheld",
        "https://example.com/?Bearer%20withheld",
        "https://example.com/?auth-token+%3D+withheld",
        "https://example.com/?Bearer+withheld",
        "https://example.com/?token%253Dwithheld",
        "https://example.com/?auth.secret%25253Dwithheld",
        "https://example.com/?account%2Fnumber%3Dwithheld",
        "https://example.com/?account%252Fnumber%253Dwithheld",
        "https://example.com/?q=account%2Fnumber%3Dwithheld",
        "https://example.com/?eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.notarealsecret",
        "https://example.com/?q=Bearer%20withheld",
        "https://example.com/?q=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.notarealsecret",
        "https://example.com/?q=api_key%3Dwithheld",
    ),
)
def test_public_https_url_rejects_sensitive_query_metadata(url: str) -> None:
    with pytest.raises(InvalidLocalBrowserProtocolError) as error:
        require_public_https_url(url)
    assert error.value.reason == "browser_url_not_public_https"


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/anything/auth-token%3Dwithheld",
        "https://example.com/anything/AUTH_TOKEN%3Awithheld",
        "https://example.com/anything/refresh_key=withheld",
        "https://example.com/anything/auth.secret=withheld",
        "https://example.com/anything/auth-token%253Dwithheld",
        "https://example.com/anything/account%2Fnumber%3Dwithheld",
        "https://example.com/anything/Bearer%20withheld",
        "https://example.com/anything/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.notarealsecret",
    ),
)
def test_public_https_url_rejects_sensitive_path_metadata(url: str) -> None:
    with pytest.raises(InvalidLocalBrowserProtocolError) as error:
        require_public_https_url(url)
    assert error.value.reason == "browser_url_not_public_https"


def test_public_https_url_preserves_normal_search_and_market_queries() -> None:
    assert require_public_https_url("https://www.google.com/search?q=TSLA+stock+news&oq=TSLA+stock+news") == (
        "https://www.google.com/search?q=TSLA+stock+news&oq=TSLA+stock+news"
    )
    assert require_public_https_url("https://finance.yahoo.com/quote/TSLA?p=TSLA") == (
        "https://finance.yahoo.com/quote/TSLA?p=TSLA"
    )
    assert require_public_https_url("https://example.com/research/token-economy/secret-sauce") == (
        "https://example.com/research/token-economy/secret-sauce"
    )


def test_browser_models_reject_sensitive_query_urls() -> None:
    with pytest.raises(ValidationError):
        BrowserPageObservation(
            target_id="target-1",
            url="https://example.com/?api_key=withheld",
            captured_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        BrowserVisibleLink(label="sensitive", url="https://example.com/?token=withheld")
