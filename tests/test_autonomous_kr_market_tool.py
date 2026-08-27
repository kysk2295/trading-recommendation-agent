from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from tests.test_kis_kr_market_projection import _price_body, _quote_body
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_social_signal_store import _signal
from tests.test_research_agent_service_cli import _config
from trading_agent.kis_auth import KisMode
from trading_agent.kis_kr_market_client import KIS_KR_MARKET_BASE_URL, KisKrMarketClient
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import (
    KisKrSessionCalendarReceipt,
    KrSessionCalendarSnapshot,
)
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_autonomous_market_models import (
    KrAutonomousMarketError,
    KrAutonomousMarketErrorReason,
    canonical_kr_autonomous_market_corroboration_json,
)
from trading_agent.kr_autonomous_market_service import (
    KrCorroborationProjectionInput,
    collect_and_project_kr_corroboration,
    project_kr_corroboration,
)
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_sources import ResearchAgentSourcePaths

_PATHS = frozenset(
    {
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
    }
)
_FORBIDDEN = (
    "headers",
    "authorization",
    "appkey",
    "appsecret",
    "token",
    "credential",
    "account",
    "account_id",
    "raw_payload",
    "raw auth response",
)


def test_canonical_tool_surface_is_bounded_and_redacted() -> None:
    # Given: the same bounded projection returned by the public live service.
    result = project_kr_corroboration(
        KrCorroborationProjectionInput(
            signal=_signal(), calendar_snapshot=_calendar(), receipts=_receipts(), observed_at=NOW
        )
    )

    # When: Task 4 serializes the Task 2 contract canonically.
    payload = canonical_kr_autonomous_market_corroboration_json(result)
    document = json.loads(payload)

    # Then: content identity and trusted-store references exist without raw source bodies.
    assert len(payload) < 6_000
    assert document["social_signal_id"] == _signal().signal_id
    assert document["calendar_snapshot_id"] == _calendar().snapshot_id
    assert len(document["receipt_sha256s"]) == 3
    assert len(document["evidence_ids"]) == 3
    assert all(term not in payload.lower() for term in _FORBIDDEN)
    assert _signal().claim_summary not in payload


def test_public_live_boundary_records_only_three_reviewed_gets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an exact current calendar and a recording wire-level KIS transport.
    config = _service_config(tmp_path)
    _append_calendar(config, open_day=True)
    cache_dir = tmp_path / "cache"
    _write_token_cache(cache_dir)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        payload = {
            "inquire-time-itemchartprice": _receipts()[0].raw_payload,
            "inquire-price": _price_body(),
            "inquire-asking-price-exp-ccn": _quote_body(accepted_hour="130404"),
        }[request.url.path.rsplit("/", 1)[-1]]
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=payload)

    client = httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )

    def create_client(mode: KisMode) -> httpx2.Client:
        assert mode is KisMode.LIVE
        return client

    monkeypatch.setattr("trading_agent.kr_autonomous_market_service.load_kis_credentials", _credentials)
    monkeypatch.setattr("trading_agent.kr_autonomous_market_service._KIS_TOKEN_CACHE_DIR", cache_dir)
    monkeypatch.setattr("trading_agent.kr_autonomous_market_service.create_kis_client", create_client)

    # When: the production public boundary collects and projects corroboration.
    result = collect_and_project_kr_corroboration(_signal(), config, NOW)

    # Then: mutation count is zero and the private DB path is exact.
    assert len(seen) == 3
    assert {request.method for request in seen} == {"GET"}
    assert {request.url.path for request in seen} == _PATHS
    assert not any(part in request.url.path for request in seen for part in ("accno", "order", "balance"))
    receipt_root = config.kr_market_receipt_root
    assert receipt_root is not None
    receipt_path = receipt_root / "005930.sqlite3"
    receipts = KisKrMarketReceiptStore(receipt_path).receipts()
    assert result.receipt_count == len(receipts) == 3
    assert all(secret not in repr(receipts) for secret in ("dummy-app", "dummy-secret", "dummy-token"))


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("missing", KrAutonomousMarketErrorReason.CALENDAR_UNAVAILABLE),
        ("closed", KrAutonomousMarketErrorReason.SESSION_UNAVAILABLE),
        ("ambiguous", KrAutonomousMarketErrorReason.CALENDAR_UNAVAILABLE),
        ("future_signal", KrAutonomousMarketErrorReason.INVALID_INPUT),
    ),
)
def test_calendar_and_signal_fail_before_fetch(
    case: str,
    reason: KrAutonomousMarketErrorReason,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a missing, closed, ambiguous calendar or future-normalized signal.
    config = _service_config(tmp_path)
    signal = _signal()
    if case == "closed":
        _append_calendar(config, open_day=False)
    elif case == "ambiguous":
        snapshot = _calendar()
        monkeypatch.setattr(
            "trading_agent.kr_autonomous_market_service.KisKrSessionCalendarStore",
            lambda path: _AmbiguousCalendarStore(path, snapshot),
        )
    elif case == "future_signal":
        signal = signal.model_copy(update={"normalized_at": NOW + dt.timedelta(seconds=1)})
    fetches: list[str] = []
    monkeypatch.setattr(
        "trading_agent.kr_autonomous_market_service.load_kis_credentials",
        lambda mode: fetches.append(mode.value) or _credentials(mode),
    )

    # When/Then: the error is stable and no credential or HTTP boundary is reached.
    with pytest.raises(KrAutonomousMarketError) as captured:
        _ = collect_and_project_kr_corroboration(signal, config, NOW)
    assert fetches == []
    assert captured.value.reason is reason
    assert all(term not in str(captured.value).lower() for term in _FORBIDDEN)


@pytest.mark.parametrize("unsafe_path", ("/stock/accno", "/stock/order", "/stock/balance"))
def test_kis_client_rejects_mutation_origins_and_redirects_before_request(unsafe_path: str) -> None:
    # Given: a recording transport wrapped by unsafe base URLs and redirect policy.
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    transport = httpx2.MockTransport(handler)

    # When/Then: existing constructor guards reject all cases before the wire.
    with (
        httpx2.Client(
            base_url=f"{KIS_KR_MARKET_BASE_URL}{unsafe_path}", transport=transport, follow_redirects=False
        ) as unsafe,
        pytest.raises(ValueError),
    ):
        _ = KisKrMarketClient(unsafe, _credentials(KisMode.LIVE), "dummy-token")
    with (
        httpx2.Client(base_url=KIS_KR_MARKET_BASE_URL, transport=transport, follow_redirects=True) as redirecting,
        pytest.raises(ValueError),
    ):
        _ = KisKrMarketClient(redirecting, _credentials(KisMode.LIVE), "dummy-token")
    assert seen == []


class _AmbiguousCalendarStore:
    def __init__(self, path: Path, snapshot: KrSessionCalendarSnapshot) -> None:
        self.path = path
        self.snapshot = snapshot

    def snapshots(self) -> tuple[KrSessionCalendarSnapshot, ...]:
        return (self.snapshot, self.snapshot)


def _service_config(tmp_path: Path) -> ResearchAgentServiceConfig:
    source = _config(tmp_path)
    paths = ResearchAgentSourcePaths.model_validate(
        source.source_paths.model_dump(mode="python") | {"kr_calendar_store": tmp_path / "calendar.sqlite3"}
    )
    return ResearchAgentServiceConfig.model_validate(
        source.model_dump(mode="python")
        | {
            "schema_version": 4,
            "browser_gateway_config": tmp_path / "browser.json",
            "kr_market_receipt_root": tmp_path / "market-receipts",
            "kr_social_signal_database": tmp_path / "signals.sqlite3",
            "source_paths": paths,
        }
    )


def _append_calendar(config: ResearchAgentServiceConfig, *, open_day: bool) -> None:
    flag = "Y" if open_day else "N"
    payload = json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "ok",
            "ctx_area_fk": "",
            "ctx_area_nk": "",
            "output": [
                {
                    "bass_dt": "20260826",
                    "wday_dvsn_cd": "3",
                    "bzdy_yn": flag,
                    "tr_day_yn": flag,
                    "opnd_yn": flag,
                    "sttl_day_yn": flag,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    receipt = KisKrSessionCalendarReceipt(
        base_date=NOW.date(),
        received_at=NOW.replace(hour=8),
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )
    calendar_path = config.source_paths.kr_calendar_store
    assert calendar_path is not None
    assert KisKrSessionCalendarStore(calendar_path).append(receipt, project_kis_kr_session_calendar(receipt))


def _credentials(mode: KisMode) -> KisCredentials:
    assert mode is KisMode.LIVE
    return KisCredentials(app_key="dummy-app", app_secret="dummy-secret")


def _write_token_cache(cache_dir: Path, *, expires_at: str = "2026-08-27T04:04:04+00:00") -> Path:
    cache_dir.mkdir(mode=0o700)
    path = cache_dir / "kis-live-token.json"
    path.write_text(json.dumps({"access_token": "dummy-token", "expires_at": expires_at}), encoding="utf-8")
    path.chmod(0o600)
    return path
