from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.ls_nws import (
    LsNwsRawFrame,
    LsNwsWireKind,
    ParsedLsNwsNews,
    parse_ls_nws_frame,
)
from trading_agent.ls_nws_collection import (
    LS_NWS_ADAPTER_VERSION,
    LsNwsCollectionInputError,
    collect_ls_nws_news,
)
from trading_agent.ls_nws_stream import LsNwsStreamUnavailableError
from trading_agent.ls_token import LsTokenTransportError

CYCLE_ID = "kr-ls-nws-fixture-001"
COLLECTION_DATE = dt.date(2026, 7, 15)
KST = dt.timezone(dt.timedelta(hours=9))
STARTED_AT = dt.datetime(2026, 7, 15, 9, 0, 59, tzinfo=KST)
REALKEY_1 = "202607150901000100000001"
REALKEY_2 = "202607150901010100000002"
PRIVATE_TITLE = "Private synthetic semiconductor headline"


class SequenceReceiver:
    __slots__ = ("calls", "responses")

    def __init__(
        self,
        responses: list[LsNwsRawFrame | None | BaseException],
    ) -> None:
        self.responses = responses
        self.calls: list[float] = []

    def receive_frame(self, timeout_seconds: float) -> LsNwsRawFrame | None:
        self.calls.append(timeout_seconds)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_collector_appends_each_raw_frame_before_parse_and_links_news(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receiver = SequenceReceiver([_frame(1), _frame(2), None])
    open_calls = 0
    parser_calls = 0

    @contextmanager
    def opener() -> Iterator[SequenceReceiver]:
        nonlocal open_calls
        open_calls += 1
        yield receiver

    def assert_raw_first(
        frame: LsNwsRawFrame,
        collection_date: dt.date,
    ) -> ParsedLsNwsNews:
        nonlocal parser_calls
        parser_calls += 1
        assert len(store.source_receipts()) == parser_calls
        return parse_ls_nws_frame(frame, collection_date=collection_date)

    result = collect_ls_nws_news(
        opener,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _parser=assert_raw_first,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert open_calls == 1
    assert parser_calls == 2
    assert result.run.source_run_id == f"{CYCLE_ID}:news"
    assert result.run.adapter_version == LS_NWS_ADAPTER_VERSION
    assert result.run.source is KrCatalystSource.NEWS
    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.failure_code is None
    assert result.run.record_count == 2
    assert result.receipt_count == 2
    assert result.new_receipt_count == 2
    assert result.catalyst_count == 2
    assert result.new_catalyst_count == 2
    assert result.new_observation_count == 2
    assert result.restarted is False
    receipts = store.source_receipts(result.run.source_run_id)
    assert [item.receipt.http_status for item in receipts] == [101, 101]
    assert [item.receipt.content_type for item in receipts] == [
        "application/json",
        "application/json",
    ]
    assert [item.receipt.request_key for item in receipts] == [
        "ls:nws:frame:000001:text",
        "ls:nws:frame:000002:binary",
    ]
    assert receipts[0].raw_payload == _frame(1).raw_payload
    catalysts = store.catalysts()
    assert [item.record.source_record_id for item in catalysts] == [
        f"ls-nws://news/{REALKEY_1}",
        f"ls-nws://news/{REALKEY_2}",
    ]
    assert all(item.record.publisher_id is None for item in catalysts)
    assert json.loads(catalysts[0].raw_payload)["title"] == PRIVATE_TITLE
    assert len(store.observation_receipts(CYCLE_ID)) == 2
    assert store.source_runs(CYCLE_ID) == (result.run,)


def test_collector_records_connected_zero_news_window_as_success(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receiver = SequenceReceiver([None])

    result = collect_ls_nws_news(
        _opener(receiver),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=1.0,
        max_frames=1,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.record_count == 0
    assert result.receipt_count == 0
    assert result.catalyst_count == 0
    assert store.source_receipts() == ()


def test_collector_stops_successfully_at_max_frame_cap(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receiver = SequenceReceiver([_frame(1), _frame(2), _frame(3)])

    result = collect_ls_nws_news(
        _opener(receiver),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=2,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.record_count == 2
    assert len(receiver.calls) == 2
    assert len(receiver.responses) == 1


def test_collector_preserves_malformed_raw_frame_in_failed_run(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    malformed = LsNwsRawFrame(
        1,
        STARTED_AT + dt.timedelta(seconds=1),
        LsNwsWireKind.TEXT,
        b"{not-json",
    )

    result = collect_ls_nws_news(
        _opener(SequenceReceiver([malformed])),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "invalid_json"
    assert result.run.record_count == 0
    assert len(store.source_receipts()) == 1
    assert store.source_receipts()[0].raw_payload == b"{not-json"


def test_collector_preserves_second_receipt_then_fails_duplicate_realkey(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    duplicate = _frame(2, realkey=REALKEY_1, published_time="090100")

    result = collect_ls_nws_news(
        _opener(SequenceReceiver([_frame(1), duplicate])),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "duplicate_news"
    assert result.run.record_count == 1
    assert len(store.source_receipts()) == 2
    assert len(store.catalysts()) == 1
    assert len(store.observation_receipts()) == 1


def test_collector_preserves_partial_news_when_stream_fails(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receiver = SequenceReceiver(
        [_frame(1), LsNwsStreamUnavailableError("private")]
    )

    result = collect_ls_nws_news(
        _opener(receiver),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "stream_unavailable"
    assert result.run.record_count == 1
    assert len(store.source_receipts()) == 1
    assert len(store.catalysts()) == 1


def test_collector_records_token_transport_failure_without_receipt(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")

    @contextmanager
    def opener() -> Iterator[SequenceReceiver]:
        raise LsTokenTransportError("private")
        yield SequenceReceiver([])

    result = collect_ls_nws_news(
        opener,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "token_error"
    assert result.run.record_count == 0
    assert result.receipt_count == 0


@pytest.mark.parametrize("failed", (False, True))
def test_terminal_source_run_restart_performs_no_open_or_append(
    tmp_path: Path,
    failed: bool,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    initial_receiver = SequenceReceiver(
        [
            LsNwsRawFrame(
                1,
                STARTED_AT + dt.timedelta(seconds=1),
                LsNwsWireKind.TEXT,
                b"{bad" if failed else _frame(1).raw_payload,
            ),
            None,
        ]
    )
    first = collect_ls_nws_news(
        _opener(initial_receiver),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )
    open_calls = 0

    @contextmanager
    def reject_opener() -> Iterator[SequenceReceiver]:
        nonlocal open_calls
        open_calls += 1
        raise AssertionError("terminal restart opened a source")
        yield SequenceReceiver([])

    second = collect_ls_nws_news(
        reject_opener,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        duration_seconds=60.0,
        max_frames=10,
        _clock=lambda: STARTED_AT,
        _monotonic=lambda: 0.0,
    )

    assert second.run == first.run
    assert second.restarted is True
    assert second.new_receipt_count == 0
    assert second.new_catalyst_count == 0
    assert second.new_observation_count == 0
    assert open_calls == 0
    assert len(store.source_runs()) == 1


@pytest.mark.parametrize(
    ("duration_seconds", "max_frames"),
    ((0.0, 1), (-1.0, 1), (86_401.0, 1), (1.0, 0), (1.0, 100_001)),
)
def test_collector_rejects_invalid_bounds_before_database_or_open(
    tmp_path: Path,
    duration_seconds: float,
    max_frames: int,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    open_calls = 0

    @contextmanager
    def opener() -> Iterator[SequenceReceiver]:
        nonlocal open_calls
        open_calls += 1
        yield SequenceReceiver([])

    with pytest.raises(LsNwsCollectionInputError):
        _ = collect_ls_nws_news(
            opener,
            KrThemeStore(database),
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            duration_seconds=duration_seconds,
            max_frames=max_frames,
            _clock=lambda: STARTED_AT,
            _monotonic=lambda: 0.0,
        )

    assert open_calls == 0
    assert not database.exists()


def _opener(receiver: SequenceReceiver):
    @contextmanager
    def open_receiver() -> Iterator[SequenceReceiver]:
        yield receiver

    return open_receiver


def _frame(
    sequence: int,
    *,
    realkey: str | None = None,
    published_time: str | None = None,
) -> LsNwsRawFrame:
    selected_realkey = (
        realkey
        if realkey is not None
        else REALKEY_1
        if sequence == 1
        else REALKEY_2
        if sequence == 2
        else f"202607150901{sequence:02d}01000000{sequence:02d}"
    )
    selected_time = (
        published_time
        if published_time is not None
        else f"0901{sequence - 1:02d}"
    )
    document = {
        "header": {"tr_cd": "NWS", "tr_key": "NWS001"},
        "body": {
            "date": "20260715",
            "code": "",
            "realkey": selected_realkey,
            "bodysize": "4200",
            "time": selected_time,
            "id": "23",
            "title": PRIVATE_TITLE,
        },
    }
    return LsNwsRawFrame(
        sequence,
        STARTED_AT + dt.timedelta(seconds=sequence + 1),
        LsNwsWireKind.TEXT if sequence % 2 else LsNwsWireKind.BINARY,
        json.dumps(document, ensure_ascii=False).encode(),
    )
