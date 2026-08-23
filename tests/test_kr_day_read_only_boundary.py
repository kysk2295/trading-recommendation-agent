from __future__ import annotations

import ast
import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_kr_market_client import (
    KIS_KR_MARKET_BASE_URL,
    KisKrMarketClient,
    KisKrMarketFetchRequest,
)
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_ranking import KisKrRankingClient, KisKrRankingKind
from trading_agent.kis_kr_session_calendar_client import (
    KisKrSessionCalendarClient,
    KisKrSessionCalendarFetchRequest,
)
from trading_agent.kr_day_read_only_boundary import (
    KR_DAY_READ_ONLY_CAPABILITIES,
    KrDayReadOnlyBoundaryError,
    require_kr_day_read_only_boundary,
    verify_kr_day_source_closure,
)
from trading_agent.ls_nws_stream import LS_NWS_STREAM_URL, open_ls_nws_stream
from trading_agent.ls_token import LsAccessToken
from trading_agent.opendart_client import OpenDartClient
from trading_agent.opendart_config import OpenDartCredentials

SEOUL = dt.timezone(dt.timedelta(hours=9))
OBSERVED_AT = dt.datetime(2026, 7, 20, 9, 4, 2, tzinfo=SEOUL)


def test_exact_manifest_is_frozen_closed_and_exposes_no_mutation_authority() -> None:
    # Given: the production KR Day provider capability manifest.
    # When: startup verifies the exact manifest and source roots.
    boundary = require_kr_day_read_only_boundary()

    # Then: only the eight reviewed evidence-read contracts are exposed.
    assert boundary.capabilities == KR_DAY_READ_ONLY_CAPABILITIES
    assert len(boundary.capabilities) == 8
    assert {(item.provider, item.method) for item in boundary.capabilities} == {
        ("kis", "GET"),
        ("ls", "WSS_SEND"),
        ("opendart", "GET"),
    }
    public_names = {name for name in dir(boundary) if not name.startswith("_")}
    assert public_names == {"capabilities", "source_files"}


@pytest.mark.parametrize("change", ("duplicate", "unknown", "forbidden"))
def test_bad_manifest_is_rejected_before_source_scan_or_provider_call(change: str) -> None:
    # Given: a closed manifest altered with a duplicate, unknown, or forbidden contract.
    capabilities = list(KR_DAY_READ_ONLY_CAPABILITIES)
    if change == "duplicate":
        capabilities.append(capabilities[0])
    elif change == "unknown":
        capabilities[-1] = capabilities[-1].__class__(
            provider="opendart",
            module="trading_agent.opendart_client",
            method="GET",
            path="/api/company.json",
        )
    else:
        capabilities[-1] = capabilities[-1].__class__(
            provider="kis",
            module="trading_agent.kis_mutation_client",
            method="POST",
            path="/stock/" + "order",
        )
    scanned = 0

    def source_scan(_: Path) -> tuple[Path, ...]:
        nonlocal scanned
        scanned += 1
        return ()

    # When: startup verifies the altered declaration.
    with pytest.raises(KrDayReadOnlyBoundaryError):
        _ = require_kr_day_read_only_boundary(
            tuple(capabilities),
            source_root=Path("unused"),
            _source_scan=source_scan,
        )

    # Then: rejection happens before any downstream action.
    assert scanned == 0


def test_source_closure_rejects_forbidden_import_and_wire_literals(tmp_path: Path) -> None:
    # Given: KR orchestration roots with mutation authority and forbidden wire contracts.
    (tmp_path / "kr_day_bad.py").write_text(
        "from trading_agent.paper_order_gate_models import PaperOrderAdmissionRequest\n",
        encoding="utf-8",
    )
    (tmp_path / "kr_theme_day_bad.py").write_text(
        'PATH = "/stock/accno"\nREGISTRATION = {"tr_type": "1"}\n',
        encoding="utf-8",
    )

    # When/Then: the AST closure verifier fails closed.
    with pytest.raises(KrDayReadOnlyBoundaryError):
        _ = verify_kr_day_source_closure(tmp_path)


@pytest.mark.parametrize("tr_type", ("1", "2"))
def test_source_closure_rejects_ls_mutation_registration_types(
    tmp_path: Path,
    tr_type: str,
) -> None:
    # Given: a KR root containing an LS account registration wire declaration.
    (tmp_path / "kr_theme_day_bad.py").write_text(
        f'REGISTRATION = {{"tr_type": "{tr_type}"}}\n',
        encoding="utf-8",
    )

    # When/Then: the structural closure rejects LS registration types 1 and 2.
    with pytest.raises(KrDayReadOnlyBoundaryError):
        _ = verify_kr_day_source_closure(tmp_path)


@pytest.mark.parametrize(
    "module",
    (
        "trading_agent.execution_store",
        "trading_agent.alpaca_http",
        "trading_agent.broker_execution_service",
    ),
)
def test_source_closure_rejects_generic_authority_imports(
    tmp_path: Path,
    module: str,
) -> None:
    # Given: a KR root importing a generic execution or Alpaca authority module.
    (tmp_path / "kr_day_bad.py").write_text(
        f"import {module}\n",
        encoding="utf-8",
    )

    # When/Then: the structural closure rejects the authority escape.
    with pytest.raises(KrDayReadOnlyBoundaryError):
        _ = verify_kr_day_source_closure(tmp_path)


def test_source_closure_accepts_deny_declarations_without_self_matching(tmp_path: Path) -> None:
    # Given: an explicit deny declaration assembled from safe literal fragments.
    (tmp_path / "kr_day_safe.py").write_text(
        "from trading_agent.day_execution_eligibility_models import Eligibility\n"
        'DENIED = ("/stock/" + "order", "Paper" + "MutationArm")\n',
        encoding="utf-8",
    )

    # When: the AST closure verifier scans the source.
    files = verify_kr_day_source_closure(tmp_path)

    # Then: the declaration itself is not a false positive.
    assert files == (tmp_path / "kr_day_safe.py",)


def test_existing_fake_transports_emit_only_manifested_read_calls() -> None:
    # Given: recording transports attached to the existing KIS, LS, and OpenDART clients.
    http_calls: list[tuple[str, str, str]] = []
    mutation_count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal mutation_count
        tr_id = request.headers.get("tr_id", "")
        http_calls.append((request.method, request.url.path, tr_id))
        mutation_count += int(request.method != "GET")
        headers = {"content-type": "application/json", "tr_cont": ""}
        return httpx2.Response(200, request=request, headers=headers, content=b'{"rt_cd":"0"}')

    credentials = KisCredentials(app_key="app-key", app_secret="app-secret")
    with httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        market = KisKrMarketClient(client, credentials, "token", _clock=lambda: OBSERVED_AT)
        for kind in KisKrMarketReceiptKind:
            minute_end = OBSERVED_AT.replace(second=0) - dt.timedelta(minutes=1)
            _ = market.fetch(
                KisKrMarketFetchRequest(
                    kind=kind,
                    symbol="005930",
                    requested_at=OBSERVED_AT,
                    minute_end_at=minute_end if kind is KisKrMarketReceiptKind.MINUTE_BARS else None,
                )
            )
        _ = KisKrSessionCalendarClient(
            client,
            credentials,
            "token",
            _clock=lambda: OBSERVED_AT,
        ).fetch(
            KisKrSessionCalendarFetchRequest(
                base_date=OBSERVED_AT.date(),
                requested_at=OBSERVED_AT,
            )
        )
        ranking = KisKrRankingClient(client, credentials, "token", _clock=lambda: OBSERVED_AT)
        for kind in KisKrRankingKind:
            _ = ranking.fetch_page(kind, page_no=1, attempt=1, tr_cont="")

    with httpx2.Client(
        base_url="https://opendart.fss.or.kr",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        _ = OpenDartClient(
            client,
            OpenDartCredentials("d" * 40),
            _clock=lambda: OBSERVED_AT,
        ).fetch_page(OBSERVED_AT.date(), page_no=1)

    ls_connection = _FakeLsConnection()

    @contextmanager
    def connector(url: str) -> Iterator[_FakeLsConnection]:
        assert url == LS_NWS_STREAM_URL
        yield ls_connection

    with open_ls_nws_stream(
        LsAccessToken("t" * 64),
        connector=connector,
        _clock=lambda: OBSERVED_AT,
    ):
        pass

    # When: the captured calls are projected into the same structural contract shape.
    observed = {
        (provider, method, path, tr_id, ws_type, ws_cd, ws_key)
        for provider, method, path, tr_id, ws_type, ws_cd, ws_key in (
            *(("kis", *call, "", "", "") for call in http_calls[:-1]),
            ("opendart", *http_calls[-1], "", "", ""),
            (
                "ls",
                "WSS_SEND",
                LS_NWS_STREAM_URL,
                "",
                json.loads(ls_connection.sent[0])["header"]["tr_type"],
                json.loads(ls_connection.sent[0])["body"]["tr_cd"],
                json.loads(ls_connection.sent[0])["body"]["tr_key"],
            ),
        )
    }
    expected = {
        (
            item.provider,
            item.method,
            item.path,
            item.tr_id or "",
            item.ws_tr_type or "",
            item.ws_tr_cd or "",
            item.ws_tr_key or "",
        )
        for item in KR_DAY_READ_ONLY_CAPABILITIES
    }

    # Then: every real adapter call is allowlisted and none is mutation-shaped.
    assert observed == expected
    assert mutation_count == 0
    assert len(ls_connection.sent) == 1


class _FakeLsConnection:
    final_url = LS_NWS_STREAM_URL

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        return "{}"


def test_boundary_module_has_no_forbidden_public_construction_surface() -> None:
    # Given: the public boundary module parsed structurally.
    path = Path("trading_agent/kr_day_read_only_boundary.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # When: public functions and classes are enumerated.
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and not node.name.startswith("_")
    }

    # Then: no generic broker or mutation construction API exists.
    assert names.isdisjoint(
        {
            "submit",
            "cancel",
            "order",
            "account",
            "balance",
            "position",
            "PaperOrderAdmissionRequest",
            "PaperMutationArm",
        }
    )
