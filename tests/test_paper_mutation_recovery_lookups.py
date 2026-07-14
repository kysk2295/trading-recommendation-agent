from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import httpx2

from tests.test_alpaca_paper_mutation_client import _oco_response
from tests.test_alpaca_paper_order_reads import _order_json
from tests.test_paper_mutation_recovery import (
    _plan,
    _record_ambiguous,
)
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_intents import (
    protective_oco_mutation_intent,
    safety_action_mutation_intent,
)
from trading_agent.paper_mutation_recovery_lookups import (
    read_paper_mutation_recovery_lookups,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperProtectiveOcoMutationLookup,
)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def test_ambiguous_oco_lookup_uses_deterministic_client_order_id(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous protective OCO mutation in the immutable ledger.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
        _record_ambiguous(
            writer,
            protective_oco_mutation_intent(
                FINGERPRINT,
                store.protective_oco_plans()[0],
            ),
        )
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json=_oco_response())

    # When: recovery gathers targeted REST evidence.
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        lookups = read_paper_mutation_recovery_lookups(
            AlpacaPaperClient(http_client, _credentials(), _clock=lambda: OBSERVED_AT),
            store.reconciliation_ledger(),
            lambda: OBSERVED_AT,
        )

    # Then: nested OCO lookup is tied to the deterministic client ID.
    assert isinstance(lookups[0], PaperProtectiveOcoMutationLookup)
    assert lookups[0].snapshot is not None
    assert requests[0].url.path == "/v2/orders:by_client_order_id"
    assert requests[0].url.params["client_order_id"] == _plan().client_order_id
    assert requests[0].url.params["nested"] == "true"


def test_ambiguous_cancel_lookup_uses_exact_broker_order_id(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous cancel mutation with an immutable broker order ID.
    store = initialized_store(tmp_path)
    action = PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False)
    plan = PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.ENTRY_CUTOFF,
        Decimal(0),
        Decimal(0),
        (action,),
    )
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(plan)
        _record_ambiguous(
            writer,
            safety_action_mutation_intent(store.paper_safety_plans()[0], 0, action),
        )
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = {
            **_order_json(),
            "id": "entry-1",
            "symbol": "AAA",
            "status": "canceled",
        }
        return httpx2.Response(200, request=request, json=payload)

    # When: recovery gathers targeted REST evidence.
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        lookups = read_paper_mutation_recovery_lookups(
            AlpacaPaperClient(http_client, _credentials()),
            store.reconciliation_ledger(),
            lambda: OBSERVED_AT,
        )

    # Then: the GET path contains the exact target broker order ID.
    assert isinstance(lookups[0], PaperCancelOrderMutationLookup)
    assert lookups[0].order is not None
    assert requests[0].url.path == "/v2/orders/entry-1"
