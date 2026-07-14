# Alpaca Paper-Only Foundation Implementation Plan

> **2026-07-14 안전 검토 반영:** 이 문서의 공개 주문 제출·취소 예시는 현재 구현을 설명하지 않는다. 실제 foundation은 Single Writer 잠금, append-only 원장, 계좌 fingerprint 결합, GET-only Alpaca client, bootstrap과 preflight까지만 공개한다. 주문 POST/DELETE는 정규장/current-bar, order-stream heartbeat, 전체 portfolio 위험 승인, 부분체결 보호청산과 EOD 평탄화가 별도 계획으로 구현·검증된 뒤에만 연다. 아래 Task 4~5의 mutation 예시는 후속 설계 참고용이며 지금 실행하면 안 된다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the paper-only configuration, risk sizing, append-only execution ledger, Alpaca REST adapter, reconciliation gate, and preflight CLI required before the first ORB paper order.

**Architecture:** Keep the existing market-data and recommendation code unchanged while adding a separate execution boundary whose base URL is the compile-time Alpaca paper endpoint. Convert a strategy recommendation into an immutable order intent, pass it through the USD 30,000 risk policy, persist it before network submission, then reconcile every broker response into an append-only SQLite event ledger.

**Tech Stack:** Python 3.12, frozen dataclasses, `StrEnum`, `httpx2`, SQLite, argparse, pytest, Ruff, basedpyright.

---

## Scope boundary

This plan covers the safety foundation and a non-trading preflight. It deliberately stops before the live-session ORB scheduler, bracket exit management, WebSocket `trade_updates`, conservative shadow fills, and automatic strategy promotion. Those are independent testable subsystems and should be implemented from separate plans after this foundation passes.

The implementation must preserve these invariants:

- `https://paper-api.alpaca.markets` is the only trading base URL.
- `https://api.alpaca.markets` is rejected before any DNS or HTTP activity.
- KIS remains read-only.
- No credential value, account number, or authentication header appears in logs or exceptions.
- An intent is committed locally before its first POST.
- Replaying the same intent never creates a second broker order.
- An unexplained broker order or position makes the preflight fail closed.

## File map

### Create

- `trading_agent/alpaca_paper_config.py`: paper endpoint and credential-path guard.
- `trading_agent/paper_execution_models.py`: immutable account, order, position, intent, and risk models.
- `trading_agent/paper_risk.py`: USD 30,000 small-account sizing and limits.
- `trading_agent/execution_store.py`: append-only order-intent and broker-event ledger.
- `trading_agent/alpaca_paper_client.py`: strictly paper-only REST adapter.
- `trading_agent/paper_reconciliation.py`: local/broker startup comparison and fail-closed result.
- `run_alpaca_paper_preflight.py`: read-only account/order/position reconciliation CLI.
- `tests/test_alpaca_paper_config.py`
- `tests/test_paper_risk.py`
- `tests/test_execution_store.py`
- `tests/test_alpaca_paper_client.py`
- `tests/test_paper_reconciliation.py`
- `tests/test_alpaca_paper_preflight.py`

### Modify

- `pyproject.toml`: include the new CLI and modules in basedpyright.
- `README.md`: add preflight usage only after the CLI exists.
- `CODEX_START_HERE.md`: point the next task at the paper-only safety preflight.

## Task 1: Lock the paper endpoint and credential path

**Files:**
- Create: `trading_agent/alpaca_paper_config.py`
- Test: `tests/test_alpaca_paper_config.py`

- [ ] **Step 1: Write the endpoint and secret-path tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.alpaca_paper_config import (
    ALPACA_PAPER_TRADING_URL,
    DEFAULT_ALPACA_PAPER_SECRET_PATH,
    NonPaperTradingEndpointError,
    require_paper_trading_url,
)


def test_paper_config_uses_separate_secret_file() -> None:
    assert DEFAULT_ALPACA_PAPER_SECRET_PATH == (
        Path.home() / ".config/trading-agent/alpaca-paper.env"
    )


def test_paper_endpoint_accepts_only_canonical_url() -> None:
    assert require_paper_trading_url(ALPACA_PAPER_TRADING_URL) == ALPACA_PAPER_TRADING_URL


@pytest.mark.parametrize(
    "url",
    (
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets/v2",
    ),
)
def test_paper_endpoint_rejects_every_noncanonical_url(url: str) -> None:
    with pytest.raises(NonPaperTradingEndpointError, match="paper 전용"):
        _ = require_paper_trading_url(url)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
uv run pytest tests/test_alpaca_paper_config.py -q
```

Expected: collection fails because `trading_agent.alpaca_paper_config` does not exist.

- [ ] **Step 3: Implement the immutable endpoint guard**

```python
from __future__ import annotations

from pathlib import Path
from typing import Final, override

ALPACA_PAPER_TRADING_URL: Final = "https://paper-api.alpaca.markets"
DEFAULT_ALPACA_PAPER_SECRET_PATH: Final = (
    Path.home() / ".config/trading-agent/alpaca-paper.env"
)


class NonPaperTradingEndpointError(ValueError):
    __slots__ = ("url",)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @override
    def __str__(self) -> str:
        return "Alpaca 거래 주소는 paper 전용 고정값이어야 합니다"


def require_paper_trading_url(url: str) -> str:
    if url != ALPACA_PAPER_TRADING_URL:
        raise NonPaperTradingEndpointError(url)
    return url
```

- [ ] **Step 4: Run the test and static checks**

Run:

```bash
uv run pytest tests/test_alpaca_paper_config.py -q
uv run ruff check trading_agent/alpaca_paper_config.py tests/test_alpaca_paper_config.py
uv run basedpyright trading_agent/alpaca_paper_config.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the endpoint guard**

```bash
git add trading_agent/alpaca_paper_config.py tests/test_alpaca_paper_config.py
git commit -m "Add hard Alpaca paper endpoint guard"
```

## Task 2: Define immutable execution and risk models

**Files:**
- Create: `trading_agent/paper_execution_models.py`
- Create: `trading_agent/paper_risk.py`
- Test: `tests/test_paper_risk.py`

- [ ] **Step 1: Write the small-account sizing tests**

```python
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from trading_agent.paper_execution_models import PaperOrderIntent, PaperOrderSide
from trading_agent.paper_risk import PaperRiskConfig, size_paper_order

NEW_YORK = ZoneInfo("America/New_York")


def _intent(entry: float = 10.0, stop: float = 9.75) -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id="orb-v1-20260714-AAA-093600",
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(2026, 7, 14, 9, 36, tzinfo=NEW_YORK),
        side=PaperOrderSide.BUY,
        entry_limit=entry,
        stop=stop,
        target_1r=entry + (entry - stop),
        target_2r=entry + 2 * (entry - stop),
    )


def test_sizing_uses_75_dollar_risk_cap() -> None:
    sized = size_paper_order(_intent(), conservative_equity=30_000.0, liquidity_cap=10_000)
    assert sized is not None
    assert sized.quantity == 300
    assert sized.planned_risk == 75.0


def test_sizing_uses_6000_dollar_notional_cap() -> None:
    sized = size_paper_order(
        _intent(entry=100.0, stop=99.0),
        conservative_equity=30_000.0,
        liquidity_cap=10_000,
    )
    assert sized is not None
    assert sized.quantity == 60


def test_sizing_reduces_risk_after_drawdown() -> None:
    sized = size_paper_order(_intent(), conservative_equity=20_000.0, liquidity_cap=10_000)
    assert sized is not None
    assert sized.quantity == 200
    assert sized.planned_risk == 50.0


def test_sizing_rejects_invalid_stop_and_zero_liquidity() -> None:
    assert size_paper_order(_intent(stop=10.0), 30_000.0, 10_000) is None
    assert size_paper_order(_intent(), 30_000.0, 0) is None
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_paper_risk.py -q
```

Expected: collection fails because the models and sizing module do not exist.

- [ ] **Step 3: Implement the immutable models**

```python
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class PaperOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BrokerOrderEventType(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    CANCELED = "canceled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    created_at: dt.datetime
    side: PaperOrderSide
    entry_limit: float
    stop: float
    target_1r: float
    target_2r: float


@dataclass(frozen=True, slots=True)
class SizedPaperOrder:
    intent: PaperOrderIntent
    quantity: int
    planned_risk: float
    notional: float


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    observed_at: dt.datetime
    status: str
    trading_blocked: bool


@dataclass(frozen=True, slots=True)
class PaperOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: PaperOrderSide
    status: str
    quantity: int
    filled_quantity: int


@dataclass(frozen=True, slots=True)
class PaperPositionSnapshot:
    symbol: str
    quantity: int
    market_value: float
```

- [ ] **Step 4: Implement the risk policy**

```python
from __future__ import annotations

import math
from dataclasses import dataclass

from trading_agent.paper_execution_models import PaperOrderIntent, SizedPaperOrder


@dataclass(frozen=True, slots=True)
class PaperRiskConfig:
    reference_equity: float = 30_000.0
    max_risk_dollars: float = 75.0
    risk_fraction: float = 0.0025
    max_notional_dollars: float = 6_000.0
    max_open_positions: int = 3
    daily_loss_limit_dollars: float = 300.0


def size_paper_order(
    intent: PaperOrderIntent,
    conservative_equity: float,
    liquidity_cap: int,
    config: PaperRiskConfig = PaperRiskConfig(),
) -> SizedPaperOrder | None:
    risk_per_share = intent.entry_limit - intent.stop
    if risk_per_share <= 0.0 or conservative_equity <= 0.0 or liquidity_cap <= 0:
        return None
    risk_budget = min(config.max_risk_dollars, conservative_equity * config.risk_fraction)
    risk_quantity = math.floor(risk_budget / risk_per_share)
    notional_quantity = math.floor(config.max_notional_dollars / intent.entry_limit)
    quantity = min(risk_quantity, notional_quantity, liquidity_cap)
    if quantity <= 0:
        return None
    return SizedPaperOrder(
        intent=intent,
        quantity=quantity,
        planned_risk=quantity * risk_per_share,
        notional=quantity * intent.entry_limit,
    )
```

- [ ] **Step 5: Run tests and static checks**

Run:

```bash
uv run pytest tests/test_paper_risk.py -q
uv run ruff check trading_agent/paper_execution_models.py trading_agent/paper_risk.py tests/test_paper_risk.py
uv run basedpyright trading_agent/paper_execution_models.py trading_agent/paper_risk.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the models and risk policy**

```bash
git add trading_agent/paper_execution_models.py trading_agent/paper_risk.py tests/test_paper_risk.py
git commit -m "Add small-account paper risk model"
```

## Task 3: Add the append-only execution ledger

**Files:**
- Create: `trading_agent/execution_store.py`
- Test: `tests/test_execution_store.py`

- [ ] **Step 1: Write idempotency and append-only tests**

```python
from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import PaperOrderIntent, PaperOrderSide


def _intent() -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id="orb-v1-20260714-AAA-093600",
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(2026, 7, 14, 9, 36, tzinfo=ZoneInfo("America/New_York")),
        side=PaperOrderSide.BUY,
        entry_limit=10.0,
        stop=9.75,
        target_1r=10.25,
        target_2r=10.5,
    )


def test_intent_is_inserted_once(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    assert store.save_intent(_intent(), quantity=300) is True
    assert store.save_intent(_intent(), quantity=300) is False
    assert len(store.intents()) == 1


def test_broker_events_are_append_only(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    _ = store.save_intent(_intent(), quantity=300)
    store.append_broker_event(
        intent_id=_intent().intent_id,
        occurred_at=_intent().created_at,
        event_type="submitted",
        broker_order_id="paper-order-1",
        payload_json='{"status":"accepted"}',
    )
    store.append_broker_event(
        intent_id=_intent().intent_id,
        occurred_at=_intent().created_at + dt.timedelta(seconds=1),
        event_type="accepted",
        broker_order_id="paper-order-1",
        payload_json='{"status":"accepted"}',
    )
    assert [event.event_type for event in store.broker_events(_intent().intent_id)] == [
        "submitted",
        "accepted",
    ]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_execution_store.py -q
```

Expected: collection fails because `ExecutionStore` does not exist.

- [ ] **Step 3: Implement the ledger schema and immutable reads**

Create two tables with these exact constraints:

```sql
CREATE TABLE IF NOT EXISTS order_intents (
  intent_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  symbol TEXT NOT NULL,
  created_at TEXT NOT NULL,
  side TEXT NOT NULL,
  entry_limit REAL NOT NULL,
  stop REAL NOT NULL,
  target_1r REAL NOT NULL,
  target_2r REAL NOT NULL,
  quantity INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_order_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  intent_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES order_intents(intent_id)
);
```

Implement `ExecutionStore.save_intent()` with `INSERT OR IGNORE`, return `True` only for the first insert, and never update an existing intent. Implement `append_broker_event()` with a plain `INSERT`; implement ordered immutable reads into frozen row dataclasses.

- [ ] **Step 4: Run tests and static checks**

Run:

```bash
uv run pytest tests/test_execution_store.py -q
uv run ruff check trading_agent/execution_store.py tests/test_execution_store.py
uv run basedpyright trading_agent/execution_store.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the ledger**

```bash
git add trading_agent/execution_store.py tests/test_execution_store.py
git commit -m "Add append-only paper execution ledger"
```

## Task 4: Implement the strictly paper-only REST adapter

**Files:**
- Create: `trading_agent/alpaca_paper_client.py`
- Test: `tests/test_alpaca_paper_client.py`

- [ ] **Step 1: Write MockTransport tests for reads and order submission**

```python
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials
from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import NonPaperTradingEndpointError
from trading_agent.paper_execution_models import (
    PaperOrderIntent,
    PaperOrderSide,
    SizedPaperOrder,
)


def _sized_order() -> SizedPaperOrder:
    intent = PaperOrderIntent(
        intent_id="orb-v1-20260714-AAA-093600",
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(
            2026,
            7,
            14,
            9,
            36,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        side=PaperOrderSide.BUY,
        entry_limit=10.0,
        stop=9.75,
        target_1r=10.25,
        target_2r=10.5,
    )
    return SizedPaperOrder(intent, quantity=300, planned_risk=75.0, notional=3_000.0)


def _paper_order_json() -> dict[str, object]:
    return {
        "id": "paper-order-1",
        "client_order_id": "orb-v1-20260714-AAA-093600",
        "symbol": "AAA",
        "side": "buy",
        "status": "accepted",
        "qty": "300",
        "filled_qty": "0",
    }


def test_client_rejects_live_base_url_before_request() -> None:
    def reject_request(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"HTTP must not run: {request.url}")

    with httpx2.Client(
        base_url="https://api.alpaca.markets",
        transport=httpx2.MockTransport(reject_request),
    ) as http_client:
        with pytest.raises(NonPaperTradingEndpointError, match="paper 전용"):
            _ = AlpacaPaperClient(
                http_client,
                AlpacaCredentials("test-key", "test-secret"),
            )


def test_account_snapshot_redacts_account_number() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v2/account"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "private-account-id",
                "account_number": "private-account-number",
                "status": "ACTIVE",
                "trading_blocked": False,
            },
        )

    observed_at = dt.datetime(2026, 7, 14, 9, 25, tzinfo=dt.UTC)
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        snapshot = AlpacaPaperClient(
            http_client,
            AlpacaCredentials("test-key", "test-secret"),
        ).account(observed_at)

    assert snapshot.status == "ACTIVE"
    assert snapshot.trading_blocked is False
    assert "private-account" not in repr(snapshot)


def test_submit_limit_order_uses_day_and_disables_extended_hours() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json=_paper_order_json())

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        result = AlpacaPaperClient(
            http_client,
            AlpacaCredentials("test-key", "test-secret"),
        ).submit_limit_order(_sized_order())

    assert result.client_order_id == _sized_order().intent.intent_id
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v2/orders"
    assert requests[0].read().decode("utf-8") == (
        '{"symbol":"AAA","qty":"300","side":"buy","type":"limit",'
        '"time_in_force":"day","limit_price":"10.0000",'
        '"extended_hours":false,"client_order_id":"orb-v1-20260714-AAA-093600"}'
    )


def test_api_failure_does_not_render_credentials() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            request=request,
            json={"message": "forbidden"},
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        client = AlpacaPaperClient(
            http_client,
            AlpacaCredentials("test-key", "test-secret"),
        )
        with pytest.raises(AlpacaApiError) as captured:
            _ = client.account(dt.datetime(2026, 7, 14, tzinfo=dt.UTC))

    rendered = str(captured.value)
    assert "403" in rendered
    assert "test-key" not in rendered
    assert "test-secret" not in rendered
```

Use `httpx2.MockTransport` as in `tests/test_alpaca_archive.py`. Capture the request and assert that the POST body is exactly:

```json
{
  "symbol": "AAA",
  "qty": "300",
  "side": "buy",
  "type": "limit",
  "time_in_force": "day",
  "limit_price": "10.0000",
  "extended_hours": false,
  "client_order_id": "orb-v1-20260714-AAA-093600"
}
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_alpaca_paper_client.py -q
```

Expected: collection fails because `AlpacaPaperClient` does not exist.

- [ ] **Step 3: Implement the adapter**

```python
from __future__ import annotations

import datetime as dt
from typing import final

import httpx2

from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials
from trading_agent.alpaca_paper_config import require_paper_trading_url
from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
    SizedPaperOrder,
)


@final
class AlpacaPaperClient:
    def __init__(self, client: httpx2.Client, credentials: AlpacaCredentials) -> None:
        _ = require_paper_trading_url(str(client.base_url).rstrip("/"))
        self._client = client
        self._credentials = credentials

    def account(self, observed_at: dt.datetime) -> PaperAccountSnapshot:
        payload = self._object(self._request("GET", "/v2/account"))
        return PaperAccountSnapshot(
            observed_at=observed_at,
            status=self._required_string(payload, "status"),
            trading_blocked=self._required_bool(payload, "trading_blocked"),
        )

    def open_orders(self) -> tuple[PaperOrderSnapshot, ...]:
        response = self._request("GET", "/v2/orders", params={"status": "open"})
        payload = response.json()
        if not isinstance(payload, list):
            raise AlpacaApiError(response.status_code, "주문 목록 형식 오류")
        return tuple(self._parse_order(self._object_value(item)) for item in payload)

    def positions(self) -> tuple[PaperPositionSnapshot, ...]:
        response = self._request("GET", "/v2/positions")
        payload = response.json()
        if not isinstance(payload, list):
            raise AlpacaApiError(response.status_code, "포지션 목록 형식 오류")
        return tuple(
            PaperPositionSnapshot(
                symbol=self._required_string(self._object_value(item), "symbol"),
                quantity=int(float(self._required_string(self._object_value(item), "qty"))),
                market_value=float(
                    self._required_string(self._object_value(item), "market_value")
                ),
            )
            for item in payload
        )

    def order_by_client_id(self, client_order_id: str) -> PaperOrderSnapshot | None:
        response = self._client.get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self._headers(),
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response)
        return self._parse_order(self._object(response))

    def submit_limit_order(self, order: SizedPaperOrder) -> PaperOrderSnapshot:
        response = self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": order.intent.symbol,
                "qty": str(order.quantity),
                "side": order.intent.side.value,
                "type": "limit",
                "time_in_force": "day",
                "limit_price": f"{order.intent.entry_limit:.4f}",
                "extended_hours": False,
                "client_order_id": order.intent.intent_id,
            },
        )
        return self._parse_order(self._object(response))

    def cancel_order(self, broker_order_id: str) -> None:
        _ = self._request("DELETE", f"/v2/orders/{broker_order_id}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> httpx2.Response:
        response = self._client.request(
            method,
            path,
            params=params,
            json=json,
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._credentials.key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
        }

    @staticmethod
    def _raise_for_status(response: httpx2.Response) -> None:
        if response.is_success:
            return
        message = "요청 실패"
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            message = str(payload["message"])
        raise AlpacaApiError(response.status_code, message)

    @staticmethod
    def _object(response: httpx2.Response) -> dict[str, object]:
        return AlpacaPaperClient._object_value(response.json())

    @staticmethod
    def _object_value(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise AlpacaApiError(200, "응답 객체 형식 오류")
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def _required_string(payload: dict[str, object], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise AlpacaApiError(200, f"필수 문자열 필드 오류: {name}")
        return value

    @staticmethod
    def _required_bool(payload: dict[str, object], name: str) -> bool:
        value = payload.get(name)
        if not isinstance(value, bool):
            raise AlpacaApiError(200, f"필수 불리언 필드 오류: {name}")
        return value

    @staticmethod
    def _parse_order(payload: dict[str, object]) -> PaperOrderSnapshot:
        required = AlpacaPaperClient._required_string
        return PaperOrderSnapshot(
            broker_order_id=required(payload, "id"),
            client_order_id=required(payload, "client_order_id"),
            symbol=required(payload, "symbol"),
            side=PaperOrderSide(required(payload, "side")),
            status=required(payload, "status"),
            quantity=int(float(required(payload, "qty"))),
            filled_quantity=int(float(required(payload, "filled_qty"))),
        )
```

Keep the authentication header dictionary private and never interpolate it into an error. The tests must prove credentials are absent from every rendered exception.

- [ ] **Step 4: Run tests and static checks**

Run:

```bash
uv run pytest tests/test_alpaca_paper_client.py -q
uv run ruff check trading_agent/alpaca_paper_client.py tests/test_alpaca_paper_client.py
uv run basedpyright trading_agent/alpaca_paper_client.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the REST adapter**

```bash
git add trading_agent/alpaca_paper_client.py tests/test_alpaca_paper_client.py
git commit -m "Add Alpaca paper REST adapter"
```

## Task 5: Make submission persist-before-POST and idempotent

**Files:**
- Modify: `trading_agent/alpaca_paper_client.py`
- Modify: `trading_agent/execution_store.py`
- Test: `tests/test_alpaca_paper_client.py`

- [ ] **Step 1: Write the crash-retry test**

```python
from trading_agent.alpaca_paper_client import submit_persisted_order
from trading_agent.execution_store import ExecutionStore


def test_submit_persisted_order_is_idempotent_after_retry(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    requests: list[httpx2.Request] = []
    submitted = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal submitted
        requests.append(request)
        if request.method == "GET":
            if not submitted:
                return httpx2.Response(404, request=request, json={"message": "not found"})
            return httpx2.Response(200, request=request, json=_paper_order_json())
        assert request.method == "POST"
        submitted = True
        return httpx2.Response(200, request=request, json=_paper_order_json())

    occurred_at = dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC)
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        client = AlpacaPaperClient(
            http_client,
            AlpacaCredentials("test-key", "test-secret"),
        )
        first = submit_persisted_order(client, store, _sized_order(), occurred_at)
        second = submit_persisted_order(client, store, _sized_order(), occurred_at)

    assert first.broker_order_id == second.broker_order_id
    assert [request.method for request in requests].count("POST") == 1
    assert len(store.intents()) == 1
    assert len(store.broker_events(_sized_order().intent.intent_id)) == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_alpaca_paper_client.py::test_submit_persisted_order_is_idempotent_after_retry -q
```

Expected: FAIL because `submit_persisted_order` is missing.

- [ ] **Step 3: Implement the orchestration function**

```python
import json
from typing import override


class MissingBrokerOrderForPersistedIntentError(RuntimeError):
    __slots__ = ("intent_id",)

    def __init__(self, intent_id: str) -> None:
        super().__init__()
        self.intent_id = intent_id

    @override
    def __str__(self) -> str:
        return f"저장된 intent의 Alpaca paper 주문을 확인할 수 없습니다: {self.intent_id}"


def safe_order_json(order: PaperOrderSnapshot) -> str:
    return json.dumps(
        {
            "broker_order_id": order.broker_order_id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "status": order.status,
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def submit_persisted_order(
    client: AlpacaPaperClient,
    store: ExecutionStore,
    order: SizedPaperOrder,
    occurred_at: dt.datetime,
) -> PaperOrderSnapshot:
    inserted = store.save_intent(order.intent, order.quantity)
    existing = client.order_by_client_id(order.intent.intent_id)
    if existing is not None:
        return existing
    if not inserted:
        raise MissingBrokerOrderForPersistedIntentError(order.intent.intent_id)
    submitted = client.submit_limit_order(order)
    store.append_broker_event(
        intent_id=order.intent.intent_id,
        occurred_at=occurred_at,
        event_type="submitted",
        broker_order_id=submitted.broker_order_id,
        payload_json=safe_order_json(submitted),
    )
    return submitted
```

The missing-broker-order error must fail closed. It must not silently POST because the first process may have reached Alpaca before crashing.

- [ ] **Step 4: Run the focused and full adapter tests**

Run:

```bash
uv run pytest tests/test_execution_store.py tests/test_alpaca_paper_client.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit idempotent submission**

```bash
git add trading_agent/alpaca_paper_client.py trading_agent/execution_store.py tests/test_alpaca_paper_client.py
git commit -m "Make paper order submission idempotent"
```

## Task 6: Add fail-closed startup reconciliation

**Files:**
- Create: `trading_agent/paper_reconciliation.py`
- Test: `tests/test_paper_reconciliation.py`

- [ ] **Step 1: Write reconciliation tests**

```python
import datetime as dt

from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_reconciliation import reconcile_paper_state


def _account(blocked: bool = False) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=blocked,
    )


def _order(client_order_id: str) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        broker_order_id="paper-order-1",
        client_order_id=client_order_id,
        symbol="AAA",
        side=PaperOrderSide.BUY,
        status="accepted",
        quantity=300,
        filled_quantity=0,
    )


def test_empty_local_and_broker_state_is_ready() -> None:
    result = reconcile_paper_state(_account(), (), (), frozenset())
    assert result.ready is True
    assert result.reasons == ()


def test_known_open_order_is_ready() -> None:
    order = _order("known-intent")
    result = reconcile_paper_state(
        _account(),
        (order,),
        (),
        frozenset({"known-intent"}),
    )
    assert result.ready is True


def test_unknown_broker_order_blocks_new_entries() -> None:
    result = reconcile_paper_state(
        _account(),
        (_order("unknown-intent"),),
        (),
        frozenset(),
    )
    assert result.ready is False
    assert result.reasons == ("알 수 없는 paper 주문: unknown-intent",)


def test_any_broker_position_blocks_foundation_preflight() -> None:
    position = PaperPositionSnapshot("AAA", quantity=10, market_value=100.0)
    result = reconcile_paper_state(_account(), (), (position,), frozenset())
    assert result.ready is False
    assert result.reasons == ("열린 paper 포지션: AAA",)


def test_trading_blocked_account_blocks_new_entries() -> None:
    result = reconcile_paper_state(_account(blocked=True), (), (), frozenset())
    assert result.ready is False
    assert result.reasons == ("Alpaca paper 계좌가 거래 차단 상태입니다",)
```

Expected result type:

```python
@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ready: bool
    reasons: tuple[str, ...]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_paper_reconciliation.py -q
```

Expected: collection fails because the reconciliation module does not exist.

- [ ] **Step 3: Implement deterministic reconciliation**

```python
from __future__ import annotations

from dataclasses import dataclass

from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ready: bool
    reasons: tuple[str, ...]


def reconcile_paper_state(
    account: PaperAccountSnapshot,
    broker_orders: tuple[PaperOrderSnapshot, ...],
    positions: tuple[PaperPositionSnapshot, ...],
    known_intent_ids: frozenset[str],
) -> ReconciliationResult:
    reasons: list[str] = []
    if account.trading_blocked:
        reasons.append("Alpaca paper 계좌가 거래 차단 상태입니다")
    if account.status != "ACTIVE":
        reasons.append(f"Alpaca paper 계좌 상태가 ACTIVE가 아닙니다: {account.status}")
    for order in broker_orders:
        if order.client_order_id not in known_intent_ids:
            reasons.append(f"알 수 없는 paper 주문: {order.client_order_id}")
    for position in positions:
        if position.quantity != 0:
            reasons.append(f"열린 paper 포지션: {position.symbol}")
    ordered_reasons = tuple(sorted(reasons))
    return ReconciliationResult(ready=not ordered_reasons, reasons=ordered_reasons)
```

- [ ] **Step 4: Run tests and static checks**

Run:

```bash
uv run pytest tests/test_paper_reconciliation.py -q
uv run ruff check trading_agent/paper_reconciliation.py tests/test_paper_reconciliation.py
uv run basedpyright trading_agent/paper_reconciliation.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit reconciliation**

```bash
git add trading_agent/paper_reconciliation.py tests/test_paper_reconciliation.py
git commit -m "Add fail-closed paper reconciliation"
```

## Task 7: Add the read-only paper preflight CLI

**Files:**
- Create: `run_alpaca_paper_preflight.py`
- Test: `tests/test_alpaca_paper_preflight.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write CLI tests**

```python
from __future__ import annotations

import datetime as dt
from pathlib import Path

import run_alpaca_paper_preflight as preflight_cli
from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperOrderSide,
    PaperOrderSnapshot,
)


def _secret(tmp_path: Path, mode: int = 0o600) -> Path:
    path = tmp_path / "alpaca-paper.env"
    path.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def _account() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=False,
    )


def test_preflight_writes_ready_report_for_empty_account(tmp_path: Path) -> None:
    output = tmp_path / "report"
    code = preflight_cli.main(
        [
            "--secret-path",
            str(_secret(tmp_path)),
            "--database",
            str(tmp_path / "execution.sqlite3"),
            "--output-dir",
            str(output),
        ],
        state_loader=lambda _: (_account(), (), ()),
    )
    report = (output / "paper_preflight_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert "준비: 예" in report
    assert "미체결 주문: 0" in report
    assert "열린 포지션: 0" in report
    assert "test-key" not in report
    assert "test-secret" not in report


def test_preflight_returns_one_for_unknown_order(tmp_path: Path) -> None:
    unknown = PaperOrderSnapshot(
        broker_order_id="paper-order-1",
        client_order_id="unknown-intent",
        symbol="AAA",
        side=PaperOrderSide.BUY,
        status="accepted",
        quantity=300,
        filled_quantity=0,
    )
    output = tmp_path / "report"
    code = preflight_cli.main(
        [
            "--secret-path",
            str(_secret(tmp_path)),
            "--database",
            str(tmp_path / "execution.sqlite3"),
            "--output-dir",
            str(output),
        ],
        state_loader=lambda _: (_account(), (unknown,), ()),
    )
    assert code == 1
    assert "알 수 없는 paper 주문" in (
        output / "paper_preflight_ko.md"
    ).read_text(encoding="utf-8")


def test_preflight_rejects_world_readable_secret(tmp_path: Path) -> None:
    code = preflight_cli.main(
        [
            "--secret-path",
            str(_secret(tmp_path, mode=0o644)),
            "--database",
            str(tmp_path / "execution.sqlite3"),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        state_loader=lambda _: (_account(), (), ()),
    )
    assert code == 2
    assert not (tmp_path / "report/paper_preflight_ko.md").exists()
```

The successful report must contain only the account alias, readiness, open-order count, position count, and reasons. It must not contain account ID, key, secret, or raw response.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/test_alpaca_paper_preflight.py -q
```

Expected: collection fails because the CLI does not exist.

- [ ] **Step 3: Implement the CLI**

Arguments:

```text
--secret-path PATH  default ~/.config/trading-agent/alpaca-paper.env
--database PATH     default outputs/paper_execution/paper_execution.sqlite3
--output-dir PATH   default outputs/paper_execution/preflight/latest
```

Implement `main(argv, state_loader=load_paper_state)` so tests can inject sanitized snapshots while the production default creates an `httpx2.Client` with `ALPACA_PAPER_TRADING_URL` and calls only `account()`, `open_orders()`, and `positions()`.

```python
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from trading_agent.alpaca_http import (
    AlpacaCredentials,
    create_alpaca_client,
    load_alpaca_credentials,
)
from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import (
    ALPACA_PAPER_TRADING_URL,
    DEFAULT_ALPACA_PAPER_SECRET_PATH,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_reconciliation import reconcile_paper_state

PaperState = tuple[
    PaperAccountSnapshot,
    tuple[PaperOrderSnapshot, ...],
    tuple[PaperPositionSnapshot, ...],
]
StateLoader = Callable[[AlpacaCredentials], PaperState]


def load_paper_state(credentials: AlpacaCredentials) -> PaperState:
    with create_alpaca_client(ALPACA_PAPER_TRADING_URL) as http_client:
        client = AlpacaPaperClient(http_client, credentials)
        observed_at = dt.datetime.now(dt.UTC)
        return client.account(observed_at), client.open_orders(), client.positions()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper 계좌 안전 대사")
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_PAPER_SECRET_PATH)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/preflight/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    state_loader: StateLoader = load_paper_state,
) -> int:
    args = _parser().parse_args(argv)
    try:
        credentials = load_alpaca_credentials(args.secret_path)
        account, orders, positions = state_loader(credentials)
        store = ExecutionStore(args.database)
        known_ids = frozenset(intent.intent_id for intent in store.intents())
        result = reconcile_paper_state(account, orders, positions, known_ids)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    lines = [
        "# Alpaca Paper 안전 대사",
        "",
        f"- 계좌 별칭: alpaca-paper",
        f"- 준비: {'예' if result.ready else '아니오'}",
        f"- 미체결 주문: {len(orders)}",
        f"- 열린 포지션: {len(positions)}",
        "- 사유:",
        *(f"  - {reason}" for reason in result.reasons),
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / "paper_preflight_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

This CLI loads permission-checked credentials, performs three GET-only reads, reconciles against the local ledger, writes the report atomically, returns 0 only when ready, 1 when state is unsafe, and 2 for configuration or provider errors.

- [ ] **Step 4: Add the new files to basedpyright include**

Add these entries to `[tool.basedpyright].include` in `pyproject.toml`:

```toml
"run_alpaca_paper_preflight.py",
"trading_agent/alpaca_paper_config.py",
"trading_agent/paper_execution_models.py",
"trading_agent/paper_risk.py",
"trading_agent/execution_store.py",
"trading_agent/alpaca_paper_client.py",
"trading_agent/paper_reconciliation.py",
```

- [ ] **Step 5: Run CLI tests, help, and bad input**

Run:

```bash
uv run pytest tests/test_alpaca_paper_preflight.py -q
uv run python run_alpaca_paper_preflight.py --help
uv run python run_alpaca_paper_preflight.py --database /tmp/not-a-directory/db.sqlite3 --secret-path /tmp/missing.env
```

Expected: tests pass, help exits 0, and missing credentials exit 2 without a traceback or secret output.

- [ ] **Step 6: Commit the preflight**

```bash
git add run_alpaca_paper_preflight.py tests/test_alpaca_paper_preflight.py pyproject.toml
git commit -m "Add Alpaca paper safety preflight"
```

## Task 8: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`

- [ ] **Step 1: Document the preflight without claiming order execution exists**

Add this command to the README only after Task 7 passes:

```bash
./run_alpaca_paper_preflight.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/preflight/latest
```

State explicitly that this command performs GET-only reconciliation and does not submit orders. Link the approved design and this implementation plan.

- [ ] **Step 2: Run the complete project verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Expected: all commands exit 0.

- [ ] **Step 3: Run a saved-event parity smoke test**

Use `httpx2.MockTransport` or a saved sanitized fixture to run the preflight with an empty paper account. Verify:

```text
ready: true
open_orders: 0
positions: 0
```

Verify the report does not contain `APCA`, `secret`, an account number, or an authorization header.

- [ ] **Step 4: Run a real paper GET-only preflight**

With `~/.config/trading-agent/alpaca-paper.env` at mode 600, run:

```bash
./run_alpaca_paper_preflight.py
```

Expected: exit 0 for an empty paper account. If it exits 1, preserve the report and resolve the unknown order/position before implementing any POST path.

- [ ] **Step 5: Commit documentation and verification evidence**

```bash
git add README.md CODEX_START_HERE.md
git commit -m "Document Alpaca paper safety preflight"
```

## Plan self-review

- Spec coverage: paper endpoint isolation, separate credentials, USD 30,000 sizing, append-only intent/event storage, idempotency, startup reconciliation, and GET-only preflight are covered.
- Deliberate exclusions: market-session scheduler, actual ORB submission orchestration, bracket exits, WebSocket updates, EOD flatten, shadow execution, registry, and self-improvement loop are separate independently testable subsystems.
- Type consistency: `PaperOrderIntent`, `SizedPaperOrder`, `PaperOrderSnapshot`, `ExecutionStore`, `AlpacaPaperClient`, and `ReconciliationResult` keep the same names throughout the plan.
- Verification: every component has a failing-test step, a passing-test step, static checks, and an atomic commit.
