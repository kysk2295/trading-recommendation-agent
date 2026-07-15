from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_schema import CREATE_KR_THEME_SCHEMA_V1
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.opendart_client import (
    OpenDartDisclosurePage,
    OpenDartRawResponse,
    parse_opendart_disclosure_page,
)
from trading_agent.opendart_collection import collect_opendart_disclosures

RECEIVED_AT = dt.datetime(2026, 7, 15, 0, 1, tzinfo=dt.UTC)
COLLECTION_DATE = dt.date(2026, 7, 15)
CYCLE_ID = "kr-dart-20260715-001"


@dataclass(slots=True)
class StubFetcher:
    pages: dict[int, OpenDartRawResponse]
    calls: list[int] = field(default_factory=list)

    def fetch_page(
        self,
        collection_date: dt.date,
        *,
        page_no: int,
    ) -> OpenDartRawResponse:
        assert collection_date == COLLECTION_DATE
        self.calls.append(page_no)
        return self.pages[page_no]


@dataclass(slots=True)
class RejectFetcher:
    calls: int = 0

    def fetch_page(
        self,
        collection_date: dt.date,
        *,
        page_no: int,
    ) -> OpenDartRawResponse:
        self.calls += 1
        raise AssertionError((collection_date, page_no))


def test_collector_paginates_raw_first_and_appends_exact_source_run(
    tmp_path: Path,
) -> None:
    page_1_items = tuple(_disclosure(index) for index in range(100))
    page_2_items = (_disclosure(100),)
    fetcher = StubFetcher(
        {
            1: _raw(1, _success_page(1, 101, 2, page_1_items)),
            2: _raw(2, _success_page(2, 101, 2, page_2_items), seconds=1),
        }
    )
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    parser_calls = 0

    def assert_receipt_precedes_parse(raw: OpenDartRawResponse) -> OpenDartDisclosurePage:
        nonlocal parser_calls
        parser_calls += 1
        assert len(store.source_receipts()) == parser_calls
        return parse_opendart_disclosure_page(raw)

    result = collect_opendart_disclosures(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _parser=assert_receipt_precedes_parse,
    )

    assert fetcher.calls == [1, 2]
    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.record_count == 101
    assert result.receipt_count == 2
    assert result.new_receipt_count == 2
    assert result.catalyst_count == 101
    assert result.new_catalyst_count == 101
    assert result.new_observation_count == 101
    assert result.restarted is False
    assert len(store.source_receipts()) == 2
    assert len(store.observation_receipts()) == 101
    assert store.source_runs() == (result.run,)
    stored = store.catalysts()[0]
    assert stored.record.source is KrCatalystSource.DART
    assert stored.record.source_record_id == "opendart://disclosure/20260715000000"
    assert stored.record.publisher_id == "00000000"
    assert stored.record.published_at is None
    assert json.loads(stored.raw_payload) == page_1_items[0]


def test_collector_records_official_no_data_as_success_zero(tmp_path: Path) -> None:
    raw = _raw(
        1,
        json.dumps({"status": "013", "message": "none"}).encode(),
    )
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")

    result = collect_opendart_disclosures(
        StubFetcher({1: raw}),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.record_count == 0
    assert result.receipt_count == 1
    assert result.catalyst_count == 0
    assert store.catalysts() == ()


def test_collector_preserves_partial_rows_when_pagination_changes(tmp_path: Path) -> None:
    page_1_items = tuple(_disclosure(index) for index in range(100))
    fetcher = StubFetcher(
        {
            1: _raw(1, _success_page(1, 101, 2, page_1_items)),
            2: _raw(
                2,
                _success_page(2, 102, 2, (_disclosure(100), _disclosure(101))),
                seconds=1,
            ),
        }
    )
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")

    result = collect_opendart_disclosures(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "pagination_changed"
    assert result.run.record_count == 100
    assert len(store.source_receipts()) == 2
    assert len(store.catalysts()) == 100


def test_collector_preserves_api_error_receipt_and_partial_observations(
    tmp_path: Path,
) -> None:
    page_1_items = tuple(_disclosure(index) for index in range(100))
    fetcher = StubFetcher(
        {
            1: _raw(1, _success_page(1, 101, 2, page_1_items)),
            2: _raw(
                2,
                json.dumps({"status": "020", "message": "private message"}).encode(),
                seconds=1,
            ),
        }
    )
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")

    result = collect_opendart_disclosures(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "opendart_020"
    assert result.run.record_count == 100
    assert len(store.source_receipts()) == 2
    assert len(store.observation_receipts()) == 100


def test_collector_rejects_duplicate_disclosure_across_pages(tmp_path: Path) -> None:
    page_1_items = tuple(_disclosure(index) for index in range(100))
    duplicate = page_1_items[0]
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")

    result = collect_opendart_disclosures(
        StubFetcher(
            {
                1: _raw(1, _success_page(1, 101, 2, page_1_items)),
                2: _raw(2, _success_page(2, 101, 2, (duplicate,)), seconds=1),
            }
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "duplicate_disclosure"
    assert result.run.record_count == 100


def test_collector_fails_closed_before_requesting_more_than_page_cap(
    tmp_path: Path,
) -> None:
    page_1_items = tuple(_disclosure(index) for index in range(100))
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    fetcher = StubFetcher(
        {1: _raw(1, _success_page(1, 10_001, 101, page_1_items))}
    )

    result = collect_opendart_disclosures(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert fetcher.calls == [1]
    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "page_limit_exceeded"
    assert result.run.record_count == 0
    assert len(store.source_receipts()) == 1


def test_terminal_source_run_restart_performs_no_fetch_or_append(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    first = collect_opendart_disclosures(
        StubFetcher(
            {1: _raw(1, _success_page(1, 1, 1, (_disclosure(1),)))}
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )
    reject = RejectFetcher()

    second = collect_opendart_disclosures(
        reject,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert second.run == first.run
    assert second.restarted is True
    assert second.new_receipt_count == 0
    assert second.new_catalyst_count == 0
    assert second.new_observation_count == 0
    assert reject.calls == 0
    assert len(store.source_runs()) == 1


def test_collector_migrates_existing_v1_ledger_before_restart_lookup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "kr-theme-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(CREATE_KR_THEME_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version = 1")
    store = KrThemeStore(path)

    result = collect_opendart_disclosures(
        StubFetcher(
            {
                1: _raw(
                    1,
                    json.dumps({"status": "013", "message": "none"}).encode(),
                )
            }
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def _raw(
    page_no: int,
    payload: bytes,
    *,
    seconds: int = 0,
) -> OpenDartRawResponse:
    return OpenDartRawResponse(
        request_key=f"opendart:list:20260715:page:{page_no}",
        requested_page=page_no,
        received_at=RECEIVED_AT + dt.timedelta(seconds=seconds),
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )


def _success_page(
    page_no: int,
    total_count: int,
    total_page: int,
    disclosures: tuple[dict[str, str], ...],
) -> bytes:
    return json.dumps(
        {
            "status": "000",
            "message": "normal",
            "page_no": page_no,
            "page_count": 100,
            "total_count": total_count,
            "total_page": total_page,
            "list": disclosures,
        },
        ensure_ascii=False,
    ).encode()


def _disclosure(index: int) -> dict[str, str]:
    return {
        "corp_cls": "K",
        "corp_name": f"Synthetic Corp {index}",
        "corp_code": f"{index:08d}",
        "stock_code": f"{index:06d}",
        "report_nm": f"Synthetic report {index}",
        "rcept_no": f"20260715{index:06d}",
        "flr_nm": f"Synthetic Corp {index}",
        "rcept_dt": "20260715",
        "rm": "",
    }
