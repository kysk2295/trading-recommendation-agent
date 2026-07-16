from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Literal, final, override

import httpx2
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import quote_headers

KIS_KR_RANKING_BASE_URL: Final = "https://openapi.koreainvestment.com:9443"
MAX_PAGES_PER_KIND: Final = 10
MAX_ATTEMPTS: Final = 2

_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_SAFE_REQUEST_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^[0-9]{6}$")
_REQUEST_TR_CONT: Final = frozenset({"", "N"})
_RESPONSE_TR_CONT: Final = frozenset({"", "M", "F"})


class UnsafeKisKrRankingEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR ranking client endpoint는 공식 live 고정값이어야 합니다"


class UnsafeKisKrRankingRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR ranking client는 redirect를 따라가면 안 됩니다"


class KisKrRankingTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KIS KR ranking 전송에 실패했습니다"


class KisKrRankingResponseError(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return f"KIS KR ranking 응답이 유효하지 않습니다: {self.failure_code}"


class KisKrRankingKind(StrEnum):
    FLUCTUATION = "fluctuation"
    VOLUME = "volume"


@dataclass(frozen=True, slots=True)
class _KisKrRankingContract:
    path: str
    tr_id: str
    params: Mapping[str, str]


_CONTRACTS: Final[Mapping[KisKrRankingKind, _KisKrRankingContract]] = {
    KisKrRankingKind.FLUCTUATION: _KisKrRankingContract(
        path="/uapi/domestic-stock/v1/ranking/fluctuation",
        tr_id="FHPST01700000",
        params={
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
        },
    ),
    KisKrRankingKind.VOLUME: _KisKrRankingContract(
        path="/uapi/domestic-stock/v1/quotations/volume-rank",
        tr_id="FHPST01710000",
        params={
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
        },
    ),
}


@dataclass(frozen=True, slots=True)
class KisKrRankingRawResponse:
    kind: KisKrRankingKind
    page_no: int
    attempt: int
    request_tr_cont: str
    response_tr_cont: str
    request_key: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not 1 <= self.page_no <= MAX_PAGES_PER_KIND
            or not 1 <= self.attempt <= MAX_ATTEMPTS
            or self.request_tr_cont not in _REQUEST_TR_CONT
            or self.response_tr_cont not in _RESPONSE_TR_CONT
            or _SAFE_REQUEST_KEY.fullmatch(self.request_key) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or not self.raw_payload
        ):
            raise ValueError("invalid KIS KR ranking raw response")


class KisKrRankingItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["KRX"]
    ranking_kind: KisKrRankingKind
    symbol: StrictStr
    name: StrictStr
    rank: int
    price_krw: Decimal
    change_pct: Decimal
    accumulated_volume: int
    prior_day_volume: int | None
    average_volume: int | None
    volume_increase_pct: Decimal | None
    accumulated_trading_value_krw: Decimal | None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        if _SYMBOL.fullmatch(value) is None:
            raise ValueError("invalid symbol")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _canonical_name(value):
            raise ValueError("invalid name")
        return value

    @field_validator("rank")
    @classmethod
    def validate_rank(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("invalid rank")
        return value

    @field_validator("price_krw", "change_pct", "volume_increase_pct", "accumulated_trading_value_krw")
    @classmethod
    def validate_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite():
            raise ValueError("invalid decimal")
        return value

    @field_validator("accumulated_volume", "prior_day_volume", "average_volume")
    @classmethod
    def validate_non_negative_int(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 0:
            raise ValueError("invalid volume")
        return value

    @field_validator("price_krw")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("invalid price")
        return value


@dataclass(frozen=True, slots=True)
class KisKrRankingPage:
    kind: KisKrRankingKind
    items: tuple[KisKrRankingItem, ...]


class _FluctuationRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stck_shrn_iscd: StrictStr
    data_rank: StrictStr
    hts_kor_isnm: StrictStr
    stck_prpr: StrictStr
    prdy_vrss: StrictStr
    prdy_vrss_sign: StrictStr
    prdy_ctrt: StrictStr
    acml_vol: StrictStr
    stck_hgpr: StrictStr
    hgpr_hour: StrictStr
    acml_hgpr_date: StrictStr
    stck_lwpr: StrictStr
    lwpr_hour: StrictStr
    acml_lwpr_date: StrictStr
    lwpr_vrss_prpr_rate: StrictStr
    dsgt_date_clpr_vrss_prpr_rate: StrictStr
    cnnt_ascn_dynu: StrictStr
    hgpr_vrss_prpr_rate: StrictStr
    cnnt_down_dynu: StrictStr
    oprc_vrss_prpr_sign: StrictStr
    oprc_vrss_prpr: StrictStr
    oprc_vrss_prpr_rate: StrictStr
    prd_rsfl: StrictStr
    prd_rsfl_rate: StrictStr


class _VolumeRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hts_kor_isnm: StrictStr
    mksc_shrn_iscd: StrictStr
    data_rank: StrictStr
    stck_prpr: StrictStr
    prdy_vrss_sign: StrictStr
    prdy_vrss: StrictStr
    prdy_ctrt: StrictStr
    acml_vol: StrictStr
    prdy_vol: StrictStr
    lstn_stcn: StrictStr
    avrg_vol: StrictStr
    n_befr_clpr_vrss_prpr_rate: StrictStr
    vol_inrt: StrictStr
    vol_tnrt: StrictStr
    nday_vol_tnrt: StrictStr
    avrg_tr_pbmn: StrictStr
    tr_pbmn_tnrt: StrictStr
    nday_tr_pbmn_tnrt: StrictStr
    acml_tr_pbmn: StrictStr


class _KisEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rt_cd: StrictStr
    msg_cd: StrictStr
    msg1: StrictStr
    output: tuple[dict[str, object], ...]


@final
class KisKrRankingClient:
    """Exact-origin read-only adapter for the two reviewed ranking contracts."""

    def __init__(
        self,
        client: httpx2.Client,
        credentials: KisCredentials,
        access_token: str,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != KIS_KR_RANKING_BASE_URL:
            raise UnsafeKisKrRankingEndpointError
        if client.follow_redirects:
            raise UnsafeKisKrRankingRedirectPolicyError
        if not access_token or not isinstance(access_token, str):
            raise KisKrRankingTransportError
        self._client = client
        self._credentials = credentials
        self._access_token = access_token
        self._clock = _clock

    def fetch_page(
        self,
        kind: KisKrRankingKind,
        *,
        page_no: int,
        attempt: int,
        tr_cont: str,
    ) -> KisKrRankingRawResponse:
        if (
            not 1 <= page_no <= MAX_PAGES_PER_KIND
            or not 1 <= attempt <= MAX_ATTEMPTS
            or tr_cont not in _REQUEST_TR_CONT
        ):
            raise KisKrRankingTransportError
        contract = _CONTRACTS[kind]
        headers = quote_headers(self._credentials, self._access_token, contract.tr_id)
        headers = {**headers, "tr_cont": tr_cont}
        try:
            response = self._client.get(
                contract.path,
                params=dict(contract.params),
                headers=headers,
            )
            received_at = self._clock()
        except httpx2.HTTPError:
            raise KisKrRankingTransportError from None
        payload = bytes(response.content)
        response_tr_cont = _normalize_response_tr_cont(
            response.headers.get("tr_cont", "")
        )
        if (
            not payload
            or not _aware(received_at)
            or response_tr_cont not in _RESPONSE_TR_CONT
        ):
            raise KisKrRankingTransportError
        request_key = _request_key(
            kind,
            page_no=page_no,
            attempt=attempt,
            request_tr_cont=tr_cont,
            response_tr_cont=response_tr_cont,
        )
        return KisKrRankingRawResponse(
            kind=kind,
            page_no=page_no,
            attempt=attempt,
            request_tr_cont=tr_cont,
            response_tr_cont=response_tr_cont,
            request_key=request_key,
            received_at=received_at,
            status_code=response.status_code,
            content_type=_response_content_type(response),
            raw_payload=payload,
        )


def parse_kis_kr_ranking_page(
    raw_response: KisKrRankingRawResponse,
) -> KisKrRankingPage:
    if raw_response.status_code != httpx2.codes.OK:
        raise KisKrRankingResponseError(f"http_{raw_response.status_code}")
    if raw_response.content_type != "application/json":
        raise KisKrRankingResponseError("content_type")
    try:
        document: object = json.loads(raw_response.raw_payload)
    except (UnicodeError, json.JSONDecodeError):
        raise KisKrRankingResponseError("invalid_json") from None
    if not isinstance(document, dict):
        raise KisKrRankingResponseError("invalid_response")
    try:
        envelope = _KisEnvelope.model_validate(document)
    except ValidationError:
        raise KisKrRankingResponseError("invalid_response") from None
    if envelope.rt_cd != "0":
        raise KisKrRankingResponseError("kis_api_error")
    items: list[KisKrRankingItem] = []
    seen_symbols: set[str] = set()
    seen_ranks: set[int] = set()
    for row in envelope.output:
        try:
            item = _project_item(raw_response.kind, row)
        except (ValidationError, ValueError, InvalidOperation):
            raise KisKrRankingResponseError("invalid_response") from None
        if item.symbol in seen_symbols:
            raise KisKrRankingResponseError("duplicate_symbol")
        if item.rank in seen_ranks:
            raise KisKrRankingResponseError("duplicate_rank")
        seen_symbols.add(item.symbol)
        seen_ranks.add(item.rank)
        items.append(item)
    return KisKrRankingPage(kind=raw_response.kind, items=tuple(items))


def canonical_kis_kr_ranking_item(item: KisKrRankingItem) -> bytes:
    return json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _project_item(
    kind: KisKrRankingKind,
    row: dict[str, object],
) -> KisKrRankingItem:
    if kind is KisKrRankingKind.FLUCTUATION:
        parsed = _FluctuationRow.model_validate(row)
        return KisKrRankingItem(
            market="KRX",
            ranking_kind=kind,
            symbol=parsed.stck_shrn_iscd,
            name=parsed.hts_kor_isnm,
            rank=_positive_int(parsed.data_rank),
            price_krw=_non_negative_decimal(parsed.stck_prpr),
            change_pct=_finite_decimal(parsed.prdy_ctrt),
            accumulated_volume=_non_negative_int(parsed.acml_vol),
            prior_day_volume=None,
            average_volume=None,
            volume_increase_pct=None,
            accumulated_trading_value_krw=None,
        )
    parsed_volume = _VolumeRow.model_validate(row)
    return KisKrRankingItem(
        market="KRX",
        ranking_kind=kind,
        symbol=parsed_volume.mksc_shrn_iscd,
        name=parsed_volume.hts_kor_isnm,
        rank=_positive_int(parsed_volume.data_rank),
        price_krw=_non_negative_decimal(parsed_volume.stck_prpr),
        change_pct=_finite_decimal(parsed_volume.prdy_ctrt),
        accumulated_volume=_non_negative_int(parsed_volume.acml_vol),
        prior_day_volume=_non_negative_int(parsed_volume.prdy_vol),
        average_volume=_non_negative_int(parsed_volume.avrg_vol),
        volume_increase_pct=_finite_decimal(parsed_volume.vol_inrt),
        accumulated_trading_value_krw=_non_negative_decimal(
            parsed_volume.acml_tr_pbmn
        ),
    )


def _request_key(
    kind: KisKrRankingKind,
    *,
    page_no: int,
    attempt: int,
    request_tr_cont: str,
    response_tr_cont: str,
) -> str:
    return (
        f"kis-kr:{kind.value}:p{page_no}:a{attempt}:"
        f"rq-{request_tr_cont.lower()}:rs-{response_tr_cont.lower()}"
    )


def _normalize_response_tr_cont(value: str) -> str:
    stripped = value.strip()
    if stripped == "":
        return ""
    upper = stripped.upper()
    if upper in {"M", "F"}:
        return upper
    return stripped


def _response_content_type(response: httpx2.Response) -> str:
    value = response.headers.get("content-type", "application/octet-stream")
    media_type = value.partition(";")[0].strip().lower()
    return (
        media_type
        if _CONTENT_TYPE.fullmatch(media_type) is not None
        else "application/octet-stream"
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_name(value: str) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= 300
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _positive_int(value: str) -> int:
    if not value.isdigit():
        raise ValueError("invalid integer")
    parsed = int(value)
    if parsed < 1:
        raise ValueError("invalid integer")
    return parsed


def _non_negative_int(value: str) -> int:
    if not value.isdigit():
        raise ValueError("invalid integer")
    return int(value)


def _finite_decimal(value: str) -> Decimal:
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError("invalid decimal")
    return parsed


def _non_negative_decimal(value: str) -> Decimal:
    parsed = _finite_decimal(value)
    if parsed < 0:
        raise ValueError("invalid decimal")
    return parsed
