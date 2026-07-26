from __future__ import annotations

import datetime as dt
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.test_kis_kr_market_projection import _receipt
from tests.test_treasury_yield_parser import FIXTURE as TREASURY_FIXTURE
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.fred_alfred_collection import FredArtifactStore, collect_fred_alfred
from trading_agent.fred_alfred_models import (
    FredAlfredRequest,
    FredRawReceipt,
    FredSourceMode,
)
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_theme_models import (
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.treasury_yield_collection import collect_treasury_yield
from trading_agent.treasury_yield_models import (
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
)
from trading_agent.treasury_yield_store import TreasuryYieldStore

NOW = dt.datetime(2026, 7, 24, 6, 10, tzinfo=dt.UTC)
RAW_CANARIES = (
    "alfred_private_token",
    "opendart_private_token",
    "kis_private_token",
    "ls_private_token",
    "treasury_private_token",
)


@dataclass(frozen=True, slots=True)
class ExpectedProvider:
    value: str
    safe_ref: str
    observed_at: dt.datetime


def build_positive_provider_outputs(outputs: Path) -> dict[str, ExpectedProvider]:
    return {
        "alfred": _build_alfred(outputs),
        "treasury": _build_treasury(outputs),
        "opendart": build_positive_kr_source(
            outputs,
            "opendart",
            KrCatalystSource.DART,
        ),
        "kis": _build_kis(outputs),
        "ls": build_positive_kr_source(outputs, "ls", KrCatalystSource.NEWS),
    }


def _build_alfred(outputs: Path) -> ExpectedProvider:
    raw = (
        Path(__file__).parent / "fixtures/fred_alfred/alfred_cpi_vintage_two.json"
    ).read_bytes()
    request = FredAlfredRequest(
        collection_id="dashboard-alfred-positive",
        source_mode=FredSourceMode.ALFRED,
        series_id="CPIAUCSL",
        observation_start=dt.date(2024, 1, 1),
        observation_end=dt.date(2024, 3, 1),
        vintage_date=dt.date(2024, 4, 1),
        limit=10,
    )
    result = collect_fred_alfred(
        _FredFetcher(raw),
        FredArtifactStore(outputs / "source_evidence/fred_alfred/alfred"),
        request,
        _clock=iter((NOW - dt.timedelta(minutes=2), NOW - dt.timedelta(minutes=1))).__next__,
    )
    snapshot = result.terminal.snapshot
    assert snapshot is not None
    return ExpectedProvider(
        "CPIAUCSL:2",
        snapshot.snapshot_id,
        NOW - dt.timedelta(minutes=2),
    )


def _build_treasury(outputs: Path) -> ExpectedProvider:
    request = TreasuryYieldRequest(
        collection_id="dashboard-treasury-positive",
        through_date=dt.date(2026, 7, 24),
    )
    store = TreasuryYieldStore(outputs / "source_evidence/treasury-yield.sqlite3")
    store.preflight_write()
    result = collect_treasury_yield(
        _TreasuryFetcher(TREASURY_FIXTURE.read_bytes()),
        store,
        request,
        _clock=iter((NOW - dt.timedelta(minutes=3), NOW - dt.timedelta(minutes=1))).__next__,
    )
    context = result.run.context
    assert context is not None
    return ExpectedProvider(
        "2026-07-22:33.00",
        context.context_id,
        NOW - dt.timedelta(minutes=2),
    )


def build_positive_kr_source(
    outputs: Path,
    provider: str,
    source: KrCatalystSource,
) -> ExpectedProvider:
    payload = f'{{"private_token":"{provider}_private_token"}}'.encode()
    payload_sha = hashlib.sha256(payload).hexdigest()
    cycle_id = f"dashboard-{provider}-positive"
    run_id = f"{cycle_id}:{source.value}"
    receipt = KrSourceReceipt(
        source_run_id=run_id,
        source=source,
        request_key=f"{provider}:positive:receipt",
        received_at=NOW - dt.timedelta(minutes=3),
        http_status=200,
        content_type="application/json",
        payload_sha256=payload_sha,
    )
    record = KrCatalystRecord(
        source=source,
        source_record_id=f"{provider}://positive/1",
        first_observed_at=NOW - dt.timedelta(minutes=2),
        content_type="application/json",
        payload_sha256=payload_sha,
    )
    observation = KrCatalystObservation(
        collection_cycle_id=cycle_id,
        catalyst_id=record.catalyst_id,
        observed_at=NOW - dt.timedelta(minutes=2),
    )
    path = outputs / f"live_sessions/{provider}/kr_theme.sqlite3"
    with KrThemeStore(path).writer() as writer:
        stored = writer.append_source_receipt(receipt, payload)
        _ = writer.append_catalyst_from_receipt(
            record,
            observation,
            payload,
            receipt_id=stored.stored.receipt.receipt_id,
            item_index=0,
        )
        _ = writer.append_source_run(
            KrSourceCollectionRun(
                source_run_id=run_id,
                collection_cycle_id=cycle_id,
                source=source,
                adapter_version=f"{provider}-positive-v1",
                started_at=NOW - dt.timedelta(minutes=4),
                completed_at=NOW - dt.timedelta(minutes=1),
                status=KrCoverageStatus.SUCCESS,
                record_count=1,
                receipt_ids=(stored.stored.receipt.receipt_id,),
                collection_date=NOW.date(),
            )
        )
    safe_ref = hashlib.sha256(stored.stored.receipt.receipt_id.encode()).hexdigest()
    return ExpectedProvider("1", safe_ref, NOW - dt.timedelta(minutes=1))


def _build_kis(outputs: Path) -> ExpectedProvider:
    store = KisKrMarketReceiptStore(
        outputs / "live_sessions/kis/market_receipts.sqlite3"
    )
    payload_hashes: list[str] = []
    for index, kind in enumerate(KisKrMarketReceiptKind, start=1):
        payload = f'{{"private_token":"kis_private_token","kind":"{kind.value}"}}'.encode()
        receipt = _receipt(kind, payload, seconds=index)
        receipt = type(receipt)(
            kind=receipt.kind,
            symbol=receipt.symbol,
            received_at=NOW - dt.timedelta(minutes=1, seconds=3 - index),
            status_code=receipt.status_code,
            content_type=receipt.content_type,
            raw_payload=receipt.raw_payload,
        )
        _ = store.append(receipt)
        payload_hashes.append(receipt.payload_sha256)
    safe_ref = hashlib.sha256("".join(sorted(payload_hashes)).encode()).hexdigest()
    return ExpectedProvider("005930:3", safe_ref, NOW - dt.timedelta(minutes=1))


class _FredFetcher:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt:
        return FredRawReceipt.from_raw(
            request_id=request.request_id,
            received_at=NOW - dt.timedelta(minutes=2),
            status_code=200,
            content_type="application/json",
            raw_payload=self._raw,
        )


class _TreasuryFetcher:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def fetch(self, request: TreasuryYieldRequest) -> TreasuryYieldRawResponse:
        return TreasuryYieldRawResponse(
            request_id=request.request_id,
            received_at=NOW - dt.timedelta(minutes=2),
            status_code=200,
            content_type="application/xml",
            raw_payload=self._raw,
        )


if __name__ == "__main__":
    destination = Path(sys.argv[1]).resolve()
    _ = build_positive_provider_outputs(destination)
    print(collect_dashboard_snapshot_v2(destination, now=NOW).model_dump_json())
