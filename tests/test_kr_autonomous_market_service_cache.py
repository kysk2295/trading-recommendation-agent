from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

import trading_agent.kr_autonomous_market_models as market_models
import trading_agent.kr_autonomous_market_service as market_service
from scr_backtest.kis_intraday import KisCredentials
from tests.test_kis_kr_market_projection import _price_body, _quote_body
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_social_signal_store import _signal
from tests.test_research_agent_service_cli import _config
from trading_agent.kis_auth import KisMode
from trading_agent.kis_kr_market_client import KIS_KR_MARKET_BASE_URL
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_sources import ResearchAgentSourcePaths


@pytest.mark.parametrize("case", ("missing", "stale", "unsafe", "symlink_dir", "provider_malformed"))
def test_public_service_separates_cache_and_market_failures(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid open service with one cache or provider boundary failure.
    config = _service_config(tmp_path)
    snapshot = _calendar()
    cache_dir = tmp_path / "cache"
    if case == "symlink_dir":
        actual_cache = tmp_path / "actual-cache"
        _ = _write_token_cache(actual_cache, "2026-08-27T04:04:04+00:00")
        cache_dir.symlink_to(actual_cache, target_is_directory=True)
    elif case != "missing":
        expires_at = "2026-08-26T04:08:04+00:00" if case == "stale" else "2026-08-27T04:04:04+00:00"
        cache_path = _write_token_cache(cache_dir, expires_at)
        if case == "unsafe":
            cache_path.chmod(0o640)
    seen: list[httpx2.Request] = []
    creates: list[KisMode] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        payload = {
            "inquire-time-itemchartprice": _receipts()[0].raw_payload,
            "inquire-price": b"{}" if case == "provider_malformed" else _price_body(),
            "inquire-asking-price-exp-ccn": _quote_body(accepted_hour="130404"),
        }[name]
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=payload)

    def create_client(mode: KisMode) -> httpx2.Client:
        creates.append(mode)
        return httpx2.Client(
            base_url=KIS_KR_MARKET_BASE_URL,
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        )

    monkeypatch.setattr(market_service, "KisKrSessionCalendarStore", lambda path: _CalendarStore(snapshot))
    monkeypatch.setattr(market_service, "load_kis_credentials", _credentials)
    monkeypatch.setattr(market_service, "_KIS_TOKEN_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_service, "create_kis_client", create_client)

    # When/Then: cache errors precede clients; malformed market data has an accurate bounded reason.
    with pytest.raises(market_models.KrAutonomousMarketError) as captured:
        _ = market_service.collect_and_project_kr_corroboration(_signal(), config, NOW)
    expected = (
        market_models.KrAutonomousMarketErrorReason.MARKET_EVIDENCE_INVALID
        if case == "provider_malformed"
        else market_models.KrAutonomousMarketErrorReason.CREDENTIAL_BOUNDARY_FAILED
    )
    assert captured.value.reason is expected
    assert len(creates) == int(case == "provider_malformed")
    assert all(request.method == "GET" for request in seen)
    if case != "provider_malformed":
        assert seen == []


class _CalendarStore:
    def __init__(self, snapshot: KrSessionCalendarSnapshot) -> None:
        self.snapshot = snapshot

    def snapshots(self) -> tuple[KrSessionCalendarSnapshot, ...]:
        return (self.snapshot,)


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


def _credentials(mode: KisMode) -> KisCredentials:
    assert mode is KisMode.LIVE
    return KisCredentials(app_key="dummy-app", app_secret="dummy-secret")


def _write_token_cache(cache_dir: Path, expires_at: str) -> Path:
    cache_dir.mkdir(mode=0o700)
    path = cache_dir / "kis-live-token.json"
    path.write_text(json.dumps({"access_token": "dummy-token", "expires_at": expires_at}), encoding="utf-8")
    path.chmod(0o600)
    return path
