from __future__ import annotations

import datetime as dt
import json

import httpx2
import pytest

from trading_agent.opendart_client import (
    OpenDartClient,
    OpenDartResponseError,
    OpenDartTransportError,
    UnsafeOpenDartEndpointError,
    UnsafeOpenDartRedirectPolicyError,
    parse_opendart_disclosure_page,
)
from trading_agent.opendart_config import OpenDartCredentials

API_KEY = "a" * 40
RECEIVED_AT = dt.datetime(2026, 7, 15, 0, 1, tzinfo=dt.UTC)


def _success_payload(
    *,
    stock_code: str = "123456",
    extra_field: str | None = None,
) -> bytes:
    disclosure: dict[str, str] = {
        "corp_cls": "K",
        "corp_name": "Synthetic Corp",
        "corp_code": "00123456",
        "stock_code": stock_code,
        "report_nm": "Synthetic supply agreement",
        "rcept_no": "20260715000001",
        "flr_nm": "Synthetic Corp",
        "rcept_dt": "20260715",
        "rm": "",
    }
    if extra_field is not None:
        disclosure["extra"] = extra_field
    return json.dumps(
        {
            "status": "000",
            "message": "normal",
            "page_no": 1,
            "page_count": 100,
            "total_count": 1,
            "total_page": 1,
            "list": [disclosure],
        }
    ).encode()


def test_client_sends_only_exact_disclosure_list_get() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            content=_success_payload(),
        )

    with httpx2.Client(
        base_url="https://opendart.fss.or.kr",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        raw = OpenDartClient(
            http_client,
            OpenDartCredentials(API_KEY),
            _clock=lambda: RECEIVED_AT,
        ).fetch_page(dt.date(2026, 7, 15), page_no=1)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "opendart.fss.or.kr"
    assert request.url.path == "/api/list.json"
    assert request.url.params["crtfc_key"] == API_KEY
    assert request.url.params["bgn_de"] == "20260715"
    assert request.url.params["end_de"] == "20260715"
    assert request.url.params["sort"] == "date"
    assert request.url.params["sort_mth"] == "asc"
    assert request.url.params["page_no"] == "1"
    assert request.url.params["page_count"] == "100"
    assert raw.request_key == "opendart:list:20260715:page:1"
    assert raw.received_at == RECEIVED_AT
    assert raw.content_type == "application/json"
    assert API_KEY not in repr(raw)
    assert _success_payload().decode() not in repr(raw)


def test_client_rejects_wrong_endpoint_and_redirect_policy_before_request() -> None:
    called = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, request=request, content=b"{}")

    with httpx2.Client(
        base_url="https://opendart.fss.or.kr.evil.example",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as wrong_client, pytest.raises(UnsafeOpenDartEndpointError):
        _ = OpenDartClient(wrong_client, OpenDartCredentials(API_KEY))
    with httpx2.Client(
        base_url="https://opendart.fss.or.kr",
        transport=httpx2.MockTransport(handle),
        follow_redirects=True,
    ) as redirect_client, pytest.raises(UnsafeOpenDartRedirectPolicyError):
        _ = OpenDartClient(redirect_client, OpenDartCredentials(API_KEY))
    assert called is False


def test_parser_accepts_strict_success_and_preserves_official_fields() -> None:
    page = parse_opendart_disclosure_page(_raw(_success_payload()))

    assert page.no_data is False
    assert page.page_no == 1
    assert page.page_count == 100
    assert page.total_count == 1
    assert page.total_page == 1
    assert len(page.disclosures) == 1
    disclosure = page.disclosures[0]
    assert disclosure.corp_cls == "K"
    assert disclosure.corp_name == "Synthetic Corp"
    assert disclosure.corp_code == "00123456"
    assert disclosure.stock_code == "123456"
    assert disclosure.report_nm == "Synthetic supply agreement"
    assert disclosure.rcept_no == "20260715000001"
    assert disclosure.rcept_dt == "20260715"


def test_parser_treats_official_no_data_status_as_success_zero() -> None:
    page = parse_opendart_disclosure_page(
        _raw(json.dumps({"status": "013", "message": "none"}).encode())
    )

    assert page.no_data is True
    assert page.disclosures == ()
    assert page.total_count == 0
    assert page.total_page == 0


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    [
        (b"not-json", "invalid_json"),
        (json.dumps({"message": "missing"}).encode(), "invalid_response"),
        (
            json.dumps({"status": "020", "message": f"limit {API_KEY}"}).encode(),
            "opendart_020",
        ),
        (
            _success_payload(stock_code="ABCDEF"),
            "invalid_response",
        ),
        (
            _success_payload(extra_field="unexpected"),
            "invalid_response",
        ),
    ],
)
def test_parser_fails_closed_without_rendering_provider_data(
    payload: bytes,
    failure_code: str,
) -> None:
    with pytest.raises(OpenDartResponseError) as captured:
        _ = parse_opendart_disclosure_page(_raw(payload))

    assert captured.value.failure_code == failure_code
    rendered = str(captured.value)
    assert API_KEY not in rendered
    assert "limit" not in rendered
    assert "Synthetic" not in rendered


def test_parser_preserves_http_and_content_type_failures_as_safe_codes() -> None:
    with pytest.raises(OpenDartResponseError) as http_error:
        _ = parse_opendart_disclosure_page(_raw(b"redirect", status_code=302))
    with pytest.raises(OpenDartResponseError) as content_error:
        _ = parse_opendart_disclosure_page(
            _raw(_success_payload(), content_type="text/html")
        )

    assert http_error.value.failure_code == "http_302"
    assert content_error.value.failure_code == "content_type"


def test_transport_failure_does_not_render_request_url_or_key() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed", request=request)

    with httpx2.Client(
        base_url="https://opendart.fss.or.kr",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        client = OpenDartClient(http_client, OpenDartCredentials(API_KEY))
        with pytest.raises(OpenDartTransportError) as captured:
            _ = client.fetch_page(dt.date(2026, 7, 15), page_no=1)

    rendered = str(captured.value)
    assert API_KEY not in rendered
    assert "http" not in rendered.lower()
    assert "opendart.fss" not in rendered


def test_client_exposes_no_mutation_methods() -> None:
    with httpx2.Client(
        base_url="https://opendart.fss.or.kr",
        follow_redirects=False,
    ) as http_client:
        client = OpenDartClient(http_client, OpenDartCredentials(API_KEY))
        public_names = {name for name in dir(client) if not name.startswith("_")}

    assert public_names == {"fetch_page"}


def _raw(
    payload: bytes,
    *,
    status_code: int = 200,
    content_type: str = "application/json",
):
    from trading_agent.opendart_client import OpenDartRawResponse

    return OpenDartRawResponse(
        request_key="opendart:list:20260715:page:1",
        requested_page=1,
        received_at=RECEIVED_AT,
        status_code=status_code,
        content_type=content_type,
        raw_payload=payload,
    )

