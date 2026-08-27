from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import httpx2

from scr_backtest.kis_intraday import KisCredentials
from tests.test_kis_kr_market_projection import _price_body, _quote_body
from tests.test_kr_autonomous_market_service import NOW, _receipts
from trading_agent import autonomous_kr_tools, kr_autonomous_market_service
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolExecutionContext
from trading_agent.kis_auth import KisMode
from trading_agent.kis_kr_market_client import KIS_KR_MARKET_BASE_URL
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar

_KIS_PATHS = (
    "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
    "/uapi/domestic-stock/v1/quotations/inquire-price",
    "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
)


def fixture_clock() -> dt.datetime:
    return NOW


def prepare_token_cache(path: Path) -> None:
    path.mkdir(mode=0o700)
    token = path / "kis-live-token.json"
    token.write_text(
        json.dumps({"access_token": "fixture-token", "expires_at": "2026-08-27T04:04:04+00:00"}),
        encoding="utf-8",
    )
    token.chmod(0o600)


def provider_calls(path: Path) -> tuple[tuple[str, str, str], ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT provider,method,path FROM provider_calls ORDER BY rowid").fetchall()
    return tuple((str(provider), str(method), str(request_path)) for provider, method, request_path in rows)


def fixture_tool_bindings(
    services: KrAutonomousToolServices,
    audit_database: Path,
    token_cache: Path,
    bar: KrCompletedMinuteBar,
) -> tuple[AutonomousToolBinding, ...]:
    config_path = services.task_database.with_name("fixture-service-config.json")
    if config_path.exists():
        assert config_path.read_text(encoding="utf-8") == services.service_config_json
    else:
        config_path.write_text(services.service_config_json, encoding="utf-8")
        config_path.chmod(0o600)
    common = {
        "browser_evidence_database": str(services.browser_evidence_database),
        "social_signal_database": str(services.social_signal_database),
        "task_database": str(services.task_database),
        "service_config_path": str(config_path),
        "trade_database": str(services.trade_database),
        "pending_plan_database": str(services.pending_plan_database),
    }
    return (
        _binding(
            "social.signal.normalize",
            {AutonomousAgentRole.MARKET_OBSERVER, AutonomousAgentRole.RESEARCH},
            {"claim_summary", "evidence_ids_json", "symbol", "theme"},
            partial(normalize_fixture_tool, **common),
        ),
        _binding(
            "kr.market.corroborate",
            {AutonomousAgentRole.OPPORTUNITY, AutonomousAgentRole.RESEARCH},
            {"signal_id", "symbol"},
            partial(
                market_fixture_tool,
                **common,
                audit_database=str(audit_database),
                token_cache=str(token_cache),
            ),
        ),
        _binding("kr.trade.plan", {AutonomousAgentRole.TRADING}, {"thesis_json"}, partial(plan_fixture_tool, **common)),
        _binding("critic.request", {AutonomousAgentRole.CRITIC}, {"plan_id"}, partial(critic_fixture_tool, **common)),
        _binding(
            "kr.virtual.execute",
            {AutonomousAgentRole.TRADING},
            {"recommendation_id"},
            partial(
                execute_fixture_tool,
                position_database=str(services.position_database),
                task_database=str(services.task_database),
                trade_database=str(services.trade_database),
            ),
        ),
        _binding(
            "kr.position.reconcile",
            {AutonomousAgentRole.POSITION},
            {"position_id"},
            partial(
                reconcile_fixture_tool,
                position_database=str(services.position_database),
                task_database=str(services.task_database),
                trade_database=str(services.trade_database),
                bar_json=bar.model_dump_json(),
            ),
        ),
    )


def normalize_fixture_tool(args: AutonomousToolArguments, context: AutonomousToolExecutionContext, **bound: str) -> str:
    with _fixture_time(NOW):
        return autonomous_kr_tools.normalize_tool(args, context, **_config_bound(bound))


def plan_fixture_tool(args: AutonomousToolArguments, context: AutonomousToolExecutionContext, **bound: str) -> str:
    with _fixture_time(NOW):
        return autonomous_kr_tools.plan_tool(args, context, **_config_bound(bound))


def critic_fixture_tool(args: AutonomousToolArguments, context: AutonomousToolExecutionContext, **bound: str) -> str:
    with _fixture_time(NOW):
        return autonomous_kr_tools.critic_tool(args, context, **_config_bound(bound))


def execute_fixture_tool(args: AutonomousToolArguments, context: AutonomousToolExecutionContext, **bound: str) -> str:
    with _fixture_time(NOW):
        return autonomous_kr_tools.execute_tool(args, context, **bound)


def reconcile_fixture_tool(
    args: AutonomousToolArguments, context: AutonomousToolExecutionContext, *, bar_json: str, **bound: str
) -> str:
    bar = KrCompletedMinuteBar.model_validate_json(bar_json)
    with _fixture_time(bar.observed_at, bar):
        return autonomous_kr_tools.reconcile_tool(args, context, **bound)


def market_fixture_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    audit_database: str,
    token_cache: str,
    **bound: str,
) -> str:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        payload = {
            _KIS_PATHS[0]: _receipts()[0].raw_payload,
            _KIS_PATHS[1]: _price_body(),
            _KIS_PATHS[2]: _quote_body(accepted_hour="130404"),
        }[request.url.path]
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=payload)

    def create_client(mode: KisMode) -> httpx2.Client:
        if mode is not KisMode.LIVE:
            raise ValueError
        return httpx2.Client(
            base_url=KIS_KR_MARKET_BASE_URL,
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        )

    originals = (
        kr_autonomous_market_service.load_kis_credentials,
        kr_autonomous_market_service.create_kis_client,
        kr_autonomous_market_service._KIS_TOKEN_CACHE_DIR,
    )
    kr_autonomous_market_service.load_kis_credentials = _fixture_credentials
    kr_autonomous_market_service.create_kis_client = create_client
    kr_autonomous_market_service._KIS_TOKEN_CACHE_DIR = Path(token_cache)
    try:
        with _fixture_time(NOW):
            return autonomous_kr_tools.corroborate_tool(args, context, **_config_bound(bound))
    finally:
        kr_autonomous_market_service.load_kis_credentials = originals[0]
        kr_autonomous_market_service.create_kis_client = originals[1]
        kr_autonomous_market_service._KIS_TOKEN_CACHE_DIR = originals[2]
        _record_calls(Path(audit_database), seen)


@contextmanager
def _fixture_time(now: dt.datetime, bar: KrCompletedMinuteBar | None = None) -> Iterator[None]:
    from trading_agent import _autonomous_kr_tool_support

    original_now = autonomous_kr_tools.utc_now
    original_bars = _autonomous_kr_tool_support.observed_completed_bars
    autonomous_kr_tools.utc_now = lambda: now
    if bar is not None:
        _autonomous_kr_tool_support.observed_completed_bars = lambda *_args: (bar,)
    try:
        yield
    finally:
        autonomous_kr_tools.utc_now = original_now
        _autonomous_kr_tool_support.observed_completed_bars = original_bars


def _fixture_credentials(mode: KisMode) -> KisCredentials:
    if mode is not KisMode.LIVE:
        raise ValueError
    return KisCredentials(app_key="fixture-app", app_secret="fixture-secret")


def _config_bound(bound: dict[str, str]) -> dict[str, str]:
    values = dict(bound)
    path = Path(values.pop("service_config_path"))
    values["service_config_json"] = path.read_text(encoding="utf-8")
    return values


def _record_calls(path: Path, requests: list[httpx2.Request]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS provider_calls(provider TEXT,method TEXT,path TEXT)")
        connection.executemany(
            "INSERT INTO provider_calls VALUES (?,?,?)",
            (("KIS", request.method, request.url.path) for request in requests),
        )
    path.chmod(0o600)


def _binding(
    name: str,
    roles: set[AutonomousAgentRole],
    arguments: set[str],
    callback,
) -> AutonomousToolBinding:
    return AutonomousToolBinding(name, frozenset(roles), frozenset(arguments), callback, ())
