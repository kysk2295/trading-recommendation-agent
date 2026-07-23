from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_contract_collection import (
    collect_alpaca_option_contracts,
)
from trading_agent.alpaca_option_contract_models import (
    OptionCatalogFailure,
    OptionCatalogStatus,
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore

STARTED = dt.datetime(2026, 7, 23, 14, 30, tzinfo=dt.UTC)
RECEIVED = STARTED + dt.timedelta(seconds=1)
COMPLETED = STARTED + dt.timedelta(seconds=2)


class _FixtureFetcher:
    __slots__ = ("_payload",)

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        return OptionContractRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/json",
            raw_payload=self._payload,
        )


def test_occ_projection_conflict_is_preserved_as_failed_terminal(
    tmp_path: Path,
) -> None:
    # Given valid provider JSON whose OCC symbol conflicts with strike metadata.
    raw_payload = json.dumps(
        {
            "option_contracts": [
                {
                    "id": "6e58f870-fe73-4583-81e4-b9a37892c36f",
                    "symbol": "AAPL260724C00200000",
                    "name": "AAPL Jul 24 2026 201 Call",
                    "status": "active",
                    "tradable": True,
                    "expiration_date": "2026-07-24",
                    "root_symbol": "AAPL",
                    "underlying_symbol": "AAPL",
                    "underlying_asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
                    "type": "call",
                    "style": "american",
                    "strike_price": "201",
                    "size": "100",
                    "multiplier": "100",
                }
            ],
            "page_token": None,
            "limit": 100,
        },
        separators=(",", ":"),
    ).encode()
    request = OptionContractCatalogRequest(
        collection_id="m6-projection-conflict",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    store = AlpacaOptionContractStore(tmp_path / "option-contracts.sqlite3")
    store.preflight_write()

    # When collection reaches canonical security-master projection.
    result = collect_alpaca_option_contracts(
        _FixtureFetcher(raw_payload),
        store,
        request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )

    # Then raw evidence precedes one immutable response-structure terminal.
    assert result.run.status is OptionCatalogStatus.FAILED
    assert result.run.failure_code is OptionCatalogFailure.RESPONSE_STRUCTURE
    assert store.counts() == (1, 1)
    assert store.receipts(request.request_id)[0].raw_payload == raw_payload
