from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_kr_ranking import (
    KisKrRankingClient,
    KisKrRankingItem,
    KisKrRankingKind,
    KisKrRankingRawResponse,
    KisKrRankingResponseError,
    KisKrRankingTransportError,
    UnsafeKisKrRankingEndpointError,
    UnsafeKisKrRankingRedirectPolicyError,
    canonical_kis_kr_ranking_item,
    parse_kis_kr_ranking_page,
)

LIVE_ORIGIN = "https://openapi.koreainvestment.com:9443"
RECEIVED_AT = dt.datetime(2026, 7, 16, 1, 0, tzinfo=dt.UTC)
TOKEN = "dummy-token"
APP_KEY = "dummy-app-key"
APP_SECRET = "dummy-app-secret"
PRIVATE_MSG = "provider private message about secret-token"


def _credentials() -> KisCredentials:
    return KisCredentials(app_key=APP_KEY, app_secret=APP_SECRET)


def _clock() -> dt.datetime:
    return RECEIVED_AT


def _raw(
    kind: KisKrRankingKind,
    payload: bytes,
    *,
    status_code: int = 200,
    content_type: str = "application/json",
    response_tr_cont: str = "",
) -> KisKrRankingRawResponse:
    return KisKrRankingRawResponse(
        kind=kind,
        page_no=1,
        attempt=1,
        request_tr_cont="",
        response_tr_cont=response_tr_cont,
        request_key=(
            f"kis-kr:{kind.value}:p1:a1:rq-:rs-{response_tr_cont.lower()}"
        ),
        received_at=RECEIVED_AT,
        status_code=status_code,
        content_type=content_type,
        raw_payload=payload,
    )


def _fluctuation_row() -> dict[str, str]:
    return {
        "stck_shrn_iscd": "005930",
        "data_rank": "1",
        "hts_kor_isnm": "Synthetic Electronics",
        "stck_prpr": "81200",
        "prdy_vrss": "2550",
        "prdy_vrss_sign": "2",
        "prdy_ctrt": "3.25",
        "acml_vol": "1500000",
        "stck_hgpr": "82000",
        "hgpr_hour": "100000",
        "acml_hgpr_date": "20260716",
        "stck_lwpr": "80000",
        "lwpr_hour": "090500",
        "acml_lwpr_date": "20260716",
        "lwpr_vrss_prpr_rate": "1.50",
        "dsgt_date_clpr_vrss_prpr_rate": "3.25",
        "cnnt_ascn_dynu": "1",
        "hgpr_vrss_prpr_rate": "-0.98",
        "cnnt_down_dynu": "0",
        "oprc_vrss_prpr_sign": "2",
        "oprc_vrss_prpr": "500",
        "oprc_vrss_prpr_rate": "0.62",
        "prd_rsfl": "2550",
        "prd_rsfl_rate": "3.25",
    }


def _volume_row() -> dict[str, str]:
    return {
        "hts_kor_isnm": "Synthetic Electronics",
        "mksc_shrn_iscd": "005930",
        "data_rank": "1",
        "stck_prpr": "81200",
        "prdy_vrss_sign": "2",
        "prdy_vrss": "2550",
        "prdy_ctrt": "3.25",
        "acml_vol": "1500000",
        "prdy_vol": "500000",
        "lstn_stcn": "5969782550",
        "avrg_vol": "600000",
        "n_befr_clpr_vrss_prpr_rate": "3.25",
        "vol_inrt": "200.00",
        "vol_tnrt": "0.03",
        "nday_vol_tnrt": "0.10",
        "avrg_tr_pbmn": "40000000000",
        "tr_pbmn_tnrt": "0.25",
        "nday_tr_pbmn_tnrt": "0.80",
        "acml_tr_pbmn": "121800000000",
    }


def _fluctuation_body() -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "0",
            "msg1": "ok",
            "output": [_fluctuation_row()],
        },
        ensure_ascii=False,
    ).encode()


def _volume_body() -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "0",
            "msg1": "ok",
            "output": [_volume_row()],
        },
        ensure_ascii=False,
    ).encode()


def test_client_fetches_only_fixed_fluctuation_contract() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8", "tr_cont": ""},
            content=b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}',
        )

    client = httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN, _clock=_clock)
    raw = fetcher.fetch_page(
        KisKrRankingKind.FLUCTUATION,
        page_no=1,
        attempt=1,
        tr_cont="",
    )

    assert raw.content_type == "application/json"
    assert raw.kind is KisKrRankingKind.FLUCTUATION
    assert raw.page_no == 1
    assert raw.attempt == 1
    assert raw.request_tr_cont == ""
    assert raw.response_tr_cont == ""
    assert raw.request_key == "kis-kr:fluctuation:p1:a1:rq-:rs-"
    assert raw.received_at == RECEIVED_AT
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/uapi/domestic-stock/v1/ranking/fluctuation"
    assert seen[0].headers["tr_id"] == "FHPST01700000"
    assert seen[0].headers["tr_cont"] == ""
    assert seen[0].headers["custtype"] == "P"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers["appkey"] == APP_KEY
    assert seen[0].headers["appsecret"] == APP_SECRET
    assert seen[0].content == b""
    params = dict(seen[0].url.params.multi_items())
    assert params == {
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": "0000",
        "fid_rank_sort_cls_code": "0",
        "fid_input_cnt_1": "0",
        "fid_prc_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
        "fid_rsfl_rate2": "",
    }


def test_client_fetches_only_fixed_volume_contract() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json", "tr_cont": "M"},
            content=b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}',
        )

    client = httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN, _clock=_clock)
    raw = fetcher.fetch_page(
        KisKrRankingKind.VOLUME,
        page_no=2,
        attempt=1,
        tr_cont="N",
    )

    assert raw.response_tr_cont == "M"
    assert raw.request_key == "kis-kr:volume:p2:a1:rq-n:rs-m"
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/uapi/domestic-stock/v1/quotations/volume-rank"
    assert seen[0].headers["tr_id"] == "FHPST01710000"
    assert seen[0].headers["tr_cont"] == "N"
    assert seen[0].headers["custtype"] == "P"
    assert seen[0].content == b""
    params = dict(seen[0].url.params.multi_items())
    assert params == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "0",
        "FID_INPUT_PRICE_2": "1000000",
        "FID_VOL_CNT": "100000",
        "FID_INPUT_DATE_1": "",
    }


def test_client_rejects_non_production_origin_and_redirects_before_request() -> None:
    called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, content=b"{}")

    with httpx2.Client(
        base_url="https://openapivts.koreainvestment.com:29443",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as paper_client, pytest.raises(UnsafeKisKrRankingEndpointError):
        _ = KisKrRankingClient(paper_client, _credentials(), TOKEN)
    with httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=True,
    ) as redirect_client, pytest.raises(UnsafeKisKrRankingRedirectPolicyError):
        _ = KisKrRankingClient(redirect_client, _credentials(), TOKEN)
    assert called is False


def test_client_bounds_page_attempt_and_request_continuation() -> None:
    called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, content=b"{}")

    client = httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN, _clock=_clock)
    with pytest.raises(KisKrRankingTransportError):
        _ = fetcher.fetch_page(
            KisKrRankingKind.FLUCTUATION,
            page_no=11,
            attempt=1,
            tr_cont="",
        )
    with pytest.raises(KisKrRankingTransportError):
        _ = fetcher.fetch_page(
            KisKrRankingKind.FLUCTUATION,
            page_no=1,
            attempt=3,
            tr_cont="",
        )
    with pytest.raises(KisKrRankingTransportError):
        _ = fetcher.fetch_page(
            KisKrRankingKind.FLUCTUATION,
            page_no=1,
            attempt=1,
            tr_cont="M",
        )
    assert called is False


def test_client_preserves_raw_body_when_response_continuation_is_unknown() -> None:
    payload = b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}'

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "application/json", "tr_cont": "X"},
            content=payload,
        )

    client = httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN, _clock=_clock)

    raw = fetcher.fetch_page(
        KisKrRankingKind.FLUCTUATION,
        page_no=1,
        attempt=1,
        tr_cont="",
    )

    assert raw.response_tr_cont == "INVALID"
    assert raw.request_key == "kis-kr:fluctuation:p1:a1:rq-:rs-invalid"
    assert raw.raw_payload == payload


def test_raw_response_rejects_request_key_that_does_not_match_metadata() -> None:
    with pytest.raises(ValueError, match="invalid KIS KR ranking raw response"):
        _ = KisKrRankingRawResponse(
            kind=KisKrRankingKind.FLUCTUATION,
            page_no=1,
            attempt=1,
            request_tr_cont="",
            response_tr_cont="",
            request_key="kis-kr:volume:p1:a1:rq-:rs-",
            received_at=RECEIVED_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=b"{}",
        )


def test_client_transport_error_does_not_render_provider_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("secret-token connect failure", request=request)

    client = httpx2.Client(
        base_url=LIVE_ORIGIN,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN, _clock=_clock)
    with pytest.raises(KisKrRankingTransportError) as captured:
        _ = fetcher.fetch_page(
            KisKrRankingKind.FLUCTUATION,
            page_no=1,
            attempt=1,
            tr_cont="",
        )
    rendered = str(captured.value)
    assert "secret-token" not in rendered
    assert TOKEN not in rendered
    assert APP_KEY not in rendered
    assert "ConnectError" not in rendered


def test_client_exposes_only_fetch_page() -> None:
    client = httpx2.Client(base_url=LIVE_ORIGIN, follow_redirects=False)
    fetcher = KisKrRankingClient(client, _credentials(), TOKEN)
    public_names = {name for name in dir(fetcher) if not name.startswith("_")}
    assert public_names == {"fetch_page"}


def test_parse_fluctuation_page_projects_reviewed_fields() -> None:
    page = parse_kis_kr_ranking_page(
        _raw(KisKrRankingKind.FLUCTUATION, _fluctuation_body())
    )
    assert page.items == (
        KisKrRankingItem(
            market="KRX",
            ranking_kind=KisKrRankingKind.FLUCTUATION,
            symbol="005930",
            name="Synthetic Electronics",
            rank=1,
            price_krw=Decimal("81200"),
            change_pct=Decimal("3.25"),
            accumulated_volume=1_500_000,
            prior_day_volume=None,
            average_volume=None,
            volume_increase_pct=None,
            accumulated_trading_value_krw=None,
        ),
    )


def test_parse_volume_page_projects_reviewed_fields() -> None:
    page = parse_kis_kr_ranking_page(_raw(KisKrRankingKind.VOLUME, _volume_body()))
    assert page.items == (
        KisKrRankingItem(
            market="KRX",
            ranking_kind=KisKrRankingKind.VOLUME,
            symbol="005930",
            name="Synthetic Electronics",
            rank=1,
            price_krw=Decimal("81200"),
            change_pct=Decimal("3.25"),
            accumulated_volume=1_500_000,
            prior_day_volume=500_000,
            average_volume=600_000,
            volume_increase_pct=Decimal("200.00"),
            accumulated_trading_value_krw=Decimal("121800000000"),
        ),
    )


def test_parse_zero_row_success() -> None:
    page = parse_kis_kr_ranking_page(
        _raw(
            KisKrRankingKind.FLUCTUATION,
            b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}',
        )
    )
    assert page.items == ()


def test_canonical_item_is_sorted_compact_json() -> None:
    item = KisKrRankingItem(
        market="KRX",
        ranking_kind=KisKrRankingKind.VOLUME,
        symbol="005930",
        name="Synthetic Electronics",
        rank=1,
        price_krw=Decimal("81200"),
        change_pct=Decimal("3.25"),
        accumulated_volume=1_500_000,
        prior_day_volume=500_000,
        average_volume=600_000,
        volume_increase_pct=Decimal("200.00"),
        accumulated_trading_value_krw=Decimal("121800000000"),
    )
    payload = canonical_kis_kr_ranking_item(item)
    assert payload == json.dumps(
        json.loads(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    document = json.loads(payload)
    assert document["ranking_kind"] == "volume"
    assert document["symbol"] == "005930"


@pytest.mark.parametrize(
    ("status_code", "content_type", "payload", "failure_code"),
    [
        (500, "application/json", b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}', "http_500"),
        (200, "text/html", b'{"rt_cd":"0","msg_cd":"0","msg1":"ok","output":[]}', "content_type"),
        (200, "application/json", b"not-json", "invalid_json"),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00001",
                    "msg1": PRIVATE_MSG,
                    "output": [],
                }
            ).encode(),
            "kis_api_error",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00001",
                    "msg1": PRIVATE_MSG,
                }
            ).encode(),
            "kis_api_error",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        {
                            **_fluctuation_row(),
                            "stck_shrn_iscd": "5930",
                        }
                    ],
                }
            ).encode(),
            "invalid_response",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        {
                            **_fluctuation_row(),
                            "hts_kor_isnm": "",
                        }
                    ],
                }
            ).encode(),
            "invalid_response",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        {
                            **_fluctuation_row(),
                            "data_rank": "0",
                        }
                    ],
                }
            ).encode(),
            "invalid_response",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        {
                            **_fluctuation_row(),
                            "stck_prpr": "not-a-number",
                        }
                    ],
                }
            ).encode(),
            "invalid_response",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        {
                            **_fluctuation_row(),
                            "extra_field": "nope",
                        }
                    ],
                }
            ).encode(),
            "invalid_response",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        _fluctuation_row(),
                        {**_fluctuation_row(), "data_rank": "2", "stck_shrn_iscd": "005930"},
                    ],
                }
            ).encode(),
            "duplicate_symbol",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "ok",
                    "output": [
                        _fluctuation_row(),
                        {**_fluctuation_row(), "data_rank": "1", "stck_shrn_iscd": "000660"},
                    ],
                }
            ).encode(),
            "duplicate_rank",
        ),
    ],
)
def test_parser_fails_closed_with_stable_codes_without_provider_text(
    status_code: int,
    content_type: str,
    payload: bytes,
    failure_code: str,
) -> None:
    with pytest.raises(KisKrRankingResponseError) as captured:
        _ = parse_kis_kr_ranking_page(
            _raw(
                KisKrRankingKind.FLUCTUATION,
                payload,
                status_code=status_code,
                content_type=content_type,
            )
        )
    assert captured.value.failure_code == failure_code
    rendered = str(captured.value)
    assert PRIVATE_MSG not in rendered
    assert "secret-token" not in rendered
    assert "Synthetic" not in rendered
    assert APP_KEY not in rendered
