from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.alpaca_models import BARS_ADAPTER
from trading_agent.alpaca_option_chain_models import (
    OptionContractType,
    OptionFeed,
    OptionGreeks,
)
from trading_agent.alpaca_option_contract_provider_models import (
    OptionExerciseStyle,
)
from trading_agent.alpaca_option_surface import (
    AlpacaOptionSurface,
    OptionSurfaceContract,
    OptionSurfaceStatus,
    publish_alpaca_option_surface,
)
from trading_agent.alpaca_sip_runtime_adapter import (
    normalize_alpaca_sip_runtime_bars,
)
from trading_agent.alpaca_sip_runtime_evidence import (
    AlpacaSipRuntimeEvidenceProjector,
    AlpacaSipRuntimeEvidenceStore,
)
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRawPage,
)
from trading_agent.us_market_data_runtime_models import (
    build_market_data_runtime_receipt,
)
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore

NY = ZoneInfo("America/New_York")
SESSION_DATE = dt.date(2026, 7, 17)
EXPIRATION_DATE = dt.date(2026, 7, 24)
INSTRUMENT_ID = "us-eq-fixture-aapl"
SYMBOL = "AAPL"
SOURCE_ID = "alpaca.sip.us_equities"


def publish_surface(
    root: Path,
    contract_type: OptionContractType,
    implied_volatilities: tuple[Decimal, ...],
    deltas: tuple[Decimal, ...],
    *,
    observed_at: dt.datetime | None = None,
) -> Path:
    surface_observed_at = observed_at or dt.datetime(
        2026,
        7,
        17,
        10,
        1,
        30,
        tzinfo=NY,
    )
    strikes = (Decimal(95), Decimal(100), Decimal(105))
    contracts = tuple(
        OptionSurfaceContract(
            instrument_id=(f"alpaca:{contract_type.value}:{int(strike):06d}"),
            provider_symbol=(
                f"AAPL260724{'C' if contract_type is OptionContractType.CALL else 'P'}{int(strike * 1_000):08d}"
            ),
            underlying_instrument_id=INSTRUMENT_ID,
            root_symbol=SYMBOL,
            expiration_date=EXPIRATION_DATE,
            strike_price=strike,
            contract_type=contract_type,
            exercise_style=OptionExerciseStyle.AMERICAN,
            multiplier=Decimal(100),
            tradable=True,
            open_interest=100,
            open_interest_date=SESSION_DATE,
            close_price=None,
            close_price_date=None,
            master_observed_at=surface_observed_at,
            snapshot_present=True,
            latest_quote=None,
            latest_trade=None,
            implied_volatility=implied_volatility,
            greeks=OptionGreeks(
                delta=delta,
                gamma=Decimal("0.01"),
                rho=Decimal("0.01"),
                theta=Decimal("-0.01"),
                vega=Decimal("0.01"),
            ),
        )
        for strike, implied_volatility, delta in zip(
            strikes,
            implied_volatilities,
            deltas,
            strict=True,
        )
    )
    token = contract_type.value
    surface = AlpacaOptionSurface(
        status=OptionSurfaceStatus.READY,
        feed=OptionFeed.INDICATIVE,
        underlying_symbol=SYMBOL,
        expiration_date=EXPIRATION_DATE,
        contract_type=contract_type,
        master_request_id=_sha(f"master-request-{token}"),
        master_run_id=_sha(f"master-run-{token}"),
        master_run_sha256=_sha(f"master-sha-{token}"),
        chain_request_id=_sha(f"chain-request-{token}"),
        chain_run_id=_sha(f"chain-run-{token}"),
        chain_run_sha256=_sha(f"chain-sha-{token}"),
        master_observed_at=surface_observed_at,
        surface_observed_at=surface_observed_at,
        master_contract_count=len(contracts),
        chain_snapshot_count=len(contracts),
        joined_contract_count=len(contracts),
        snapshot_coverage_bps=10_000,
        open_interest_count=len(contracts),
        quote_count=0,
        trade_count=0,
        implied_volatility_count=len(contracts),
        greeks_count=len(contracts),
        contracts=contracts,
    )
    return publish_alpaca_option_surface(root / token, surface)[0]


def publish_spot_inputs(
    tmp_path: Path,
    *,
    received_at: dt.datetime | None = None,
    runtime_close: Decimal | None = None,
) -> tuple[Path, Path]:
    started_at = dt.datetime(2026, 7, 17, 9, 30, tzinfo=NY)
    bar_started_at = dt.datetime(2026, 7, 17, 10, 0, tzinfo=NY)
    raw_response = json.dumps(
        {
            "bars": {
                SYMBOL: [
                    {
                        "t": bar_started_at.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
                        "o": 100.0,
                        "h": 100.5,
                        "l": 99.5,
                        "c": 100.0,
                        "v": 123,
                        "n": 10,
                        "vw": 100.0,
                    }
                ]
            },
            "next_page_token": None,
        },
        separators=(",", ":"),
    ).encode()
    request = AlpacaSipMinutePageRequest(
        SESSION_DATE,
        SYMBOL,
        started_at,
        bar_started_at + dt.timedelta(minutes=1) - dt.timedelta(microseconds=1),
    )
    page_set = AlpacaSipMinutePage(
        request,
        (
            AlpacaSipRawPage(
                0,
                None,
                received_at or bar_started_at + dt.timedelta(minutes=1, seconds=1),
                raw_response,
                BARS_ADAPTER.validate_json(raw_response),
            ),
        ),
    )
    bars = normalize_alpaca_sip_runtime_bars(
        page_set,
        started_at,
        bar_started_at + dt.timedelta(minutes=1),
    )
    evidence = AlpacaSipRuntimeEvidenceStore(tmp_path / "sip-evidence.sqlite3")
    _ = AlpacaSipRuntimeEvidenceProjector(
        evidence,
        tmp_path / "canonical",
    ).project(page_set, INSTRUMENT_ID, bars)
    runtime_path = tmp_path / "runtime.sqlite3"
    runtime = MarketDataRuntimeStore(runtime_path)
    with runtime.writer() as writer:
        _ = writer.append_receipt(
            build_market_data_runtime_receipt(
                source_id=SOURCE_ID,
                connection_epoch="fixture-epoch",
                sequence=bars[0].sequence,
                received_at=page_set.pages[0].received_at,
                raw_payload=bars[0].canonical_payload,
                instrument_id=INSTRUMENT_ID,
                symbol=SYMBOL,
                completed_bar=(
                    bars[0].completed_bar
                    if runtime_close is None
                    else replace(
                        bars[0].completed_bar,
                        close=runtime_close,
                        high=max(
                            bars[0].completed_bar.high,
                            runtime_close,
                        ),
                    )
                ),
            )
        )
    dataset = next((tmp_path / "canonical").rglob("events.parquet")).parent
    return runtime_path, dataset


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
