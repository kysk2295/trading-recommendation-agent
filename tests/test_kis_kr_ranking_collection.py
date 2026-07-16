from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from trading_agent.kis_kr_ranking import (
    KisKrRankingKind,
    KisKrRankingRawResponse,
    KisKrRankingTransportError,
    parse_kis_kr_ranking_page,
)
from trading_agent.kis_kr_ranking_collection import (
    KIS_KR_RANKING_ADAPTER_VERSION,
    collect_kis_kr_rankings,
)
from trading_agent.kr_source_collection_models import KrSourceReceipt
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

COLLECTION_DATE = dt.date(2026, 7, 16)
RECEIVED_AT = dt.datetime(2026, 7, 16, 10, 1, tzinfo=dt.timezone(dt.timedelta(hours=9)))
CYCLE_ID = "kr-kis-ranking-001"
SOURCE_RUN_ID = f"{CYCLE_ID}:kis_ranking"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kis_kr_ranking"


class SequenceFetcher:
    def __init__(self, responses: list[KisKrRankingRawResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[KisKrRankingKind, int, int, str]] = []

    def fetch_page(
        self,
        kind: KisKrRankingKind,
        *,
        page_no: int,
        attempt: int,
        tr_cont: str,
    ) -> KisKrRankingRawResponse:
        self.calls.append((kind, page_no, attempt, tr_cont))
        if not self._responses:
            raise AssertionError("unexpected fetch")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RejectingFetcher:
    def fetch_page(
        self,
        kind: KisKrRankingKind,
        *,
        page_no: int,
        attempt: int,
        tr_cont: str,
    ) -> KisKrRankingRawResponse:
        raise AssertionError("network must not open")


def test_collection_commits_receipt_before_parser_and_closes_success(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    fetcher = SequenceFetcher(
        [
            _raw(KisKrRankingKind.FLUCTUATION, _payload("fluctuation")),
            _raw(
                KisKrRankingKind.VOLUME,
                _payload("volume"),
                received_at=RECEIVED_AT + dt.timedelta(minutes=1),
            ),
        ]
    )
    parser_calls = 0
    sleeps: list[float] = []

    def parser(raw: KisKrRankingRawResponse):  # type: ignore[no-untyped-def]
        nonlocal parser_calls
        parser_calls += 1
        assert len(store.source_receipts(SOURCE_RUN_ID)) == parser_calls
        return parse_kis_kr_ranking_page(raw)

    result = collect_kis_kr_rankings(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _parser=parser,
        _sleeper=sleeps.append,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.source is KrCatalystSource.KIS_RANKING
    assert result.run.adapter_version == KIS_KR_RANKING_ADAPTER_VERSION
    assert result.run.collection_date == COLLECTION_DATE
    assert result.run.record_count == 2
    assert result.receipt_count == 2
    assert result.new_receipt_count == 2
    assert result.catalyst_count == 2
    assert result.new_catalyst_count == 2
    assert result.new_observation_count == 2
    assert result.restarted is False
    assert fetcher.calls == [
        (KisKrRankingKind.FLUCTUATION, 1, 1, ""),
        (KisKrRankingKind.VOLUME, 1, 1, ""),
    ]
    assert sleeps == [0.08]
    assert len(store.catalysts()) == 2
    assert len(store.observations()) == 2
    assert len(store.observation_receipts()) == 2
    assert len(store.source_runs()) == 1


def test_terminal_replay_does_not_open_fetcher(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    first = collect_kis_kr_rankings(
        SequenceFetcher(
            [
                _raw(KisKrRankingKind.FLUCTUATION, _payload("fluctuation")),
                _raw(KisKrRankingKind.VOLUME, _payload("volume")),
            ]
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    replay = collect_kis_kr_rankings(
        RejectingFetcher(),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    assert replay.run == first.run
    assert replay.receipt_count == 2
    assert replay.new_receipt_count == 0
    assert replay.catalyst_count == 2
    assert replay.new_catalyst_count == 0
    assert replay.new_observation_count == 0
    assert replay.restarted is True


def test_transient_http_retries_once_after_raw_receipt(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    fetcher = SequenceFetcher(
        [
            _raw(
                KisKrRankingKind.FLUCTUATION,
                b'{"private":"first server response"}',
                status_code=503,
            ),
            _raw(
                KisKrRankingKind.FLUCTUATION,
                _payload("fluctuation"),
                attempt=2,
                received_at=RECEIVED_AT + dt.timedelta(seconds=1),
            ),
            _raw(
                KisKrRankingKind.VOLUME,
                _payload("volume"),
                received_at=RECEIVED_AT + dt.timedelta(seconds=2),
            ),
        ]
    )
    sleeps: list[float] = []

    result = collect_kis_kr_rankings(
        fetcher,
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=sleeps.append,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.receipt_count == 3
    assert result.run.record_count == 2
    assert fetcher.calls == [
        (KisKrRankingKind.FLUCTUATION, 1, 1, ""),
        (KisKrRankingKind.FLUCTUATION, 1, 2, ""),
        (KisKrRankingKind.VOLUME, 1, 1, ""),
    ]
    assert sleeps == [0.08, 0.08]


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_terminal_http_failure_is_not_retried_again(
    tmp_path: Path,
    status_code: int,
) -> None:
    responses = [
        _raw(
            KisKrRankingKind.FLUCTUATION,
            b'{"private":"provider failure"}',
            status_code=status_code,
        )
    ]
    if status_code != 429:
        responses.append(
            _raw(
                KisKrRankingKind.FLUCTUATION,
                b'{"private":"provider failure again"}',
                status_code=status_code,
                attempt=2,
                received_at=RECEIVED_AT + dt.timedelta(seconds=1),
            )
        )
    fetcher = SequenceFetcher(responses)
    sleeps: list[float] = []

    result = collect_kis_kr_rankings(
        fetcher,
        KrThemeStore(tmp_path / "kr.sqlite3"),
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=sleeps.append,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == f"http_{status_code}"
    assert result.run.record_count == 0
    assert len(fetcher.calls) == (1 if status_code == 429 else 2)
    assert sleeps == ([] if status_code == 429 else [0.08])


def test_pagination_uses_n_header_and_keeps_kind_scopes_separate(
    tmp_path: Path,
) -> None:
    fetcher = SequenceFetcher(
        [
            _raw(
                KisKrRankingKind.FLUCTUATION,
                _payload("fluctuation", symbol="005930", rank=1),
                response_tr_cont="M",
            ),
            _raw(
                KisKrRankingKind.FLUCTUATION,
                _payload("fluctuation", symbol="000660", rank=2),
                page_no=2,
                request_tr_cont="N",
                received_at=RECEIVED_AT + dt.timedelta(seconds=1),
            ),
            _raw(
                KisKrRankingKind.VOLUME,
                _payload("volume", symbol="005930", rank=1),
                received_at=RECEIVED_AT + dt.timedelta(seconds=2),
            ),
        ]
    )

    result = collect_kis_kr_rankings(
        fetcher,
        KrThemeStore(tmp_path / "kr.sqlite3"),
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.record_count == 3
    assert fetcher.calls == [
        (KisKrRankingKind.FLUCTUATION, 1, 1, ""),
        (KisKrRankingKind.FLUCTUATION, 2, 1, "N"),
        (KisKrRankingKind.VOLUME, 1, 1, ""),
    ]


def test_cross_page_duplicate_fails_without_appending_second_page_items(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    result = collect_kis_kr_rankings(
        SequenceFetcher(
            [
                _raw(
                    KisKrRankingKind.FLUCTUATION,
                    _payload("fluctuation", symbol="005930", rank=1),
                    response_tr_cont="M",
                ),
                _raw(
                    KisKrRankingKind.FLUCTUATION,
                    _payload("fluctuation", symbol="005930", rank=2),
                    page_no=2,
                    request_tr_cont="N",
                    received_at=RECEIVED_AT + dt.timedelta(seconds=1),
                ),
            ]
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "duplicate_symbol"
    assert result.run.record_count == 1
    assert result.receipt_count == 2
    assert len(store.catalysts()) == 1


def test_partial_parse_failure_preserves_prior_observations(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    result = collect_kis_kr_rankings(
        SequenceFetcher(
            [
                _raw(KisKrRankingKind.FLUCTUATION, _payload("fluctuation")),
                _raw(
                    KisKrRankingKind.VOLUME,
                    b"not-json-private-provider-body",
                    received_at=RECEIVED_AT + dt.timedelta(seconds=1),
                ),
            ]
        ),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "invalid_json"
    assert result.run.record_count == 1
    assert result.receipt_count == 2
    assert len(store.observations()) == 1


def test_mismatched_observation_date_and_invalid_continuation_are_raw_first(
    tmp_path: Path,
) -> None:
    for suffix, raw, failure_code in (
        (
            "date",
            _raw(
                KisKrRankingKind.FLUCTUATION,
                _payload("fluctuation"),
                received_at=RECEIVED_AT - dt.timedelta(days=1),
            ),
            "observation_date_mismatch",
        ),
        (
            "continuation",
            _raw(
                KisKrRankingKind.FLUCTUATION,
                _payload("fluctuation"),
                response_tr_cont="INVALID",
            ),
            "invalid_continuation",
        ),
    ):
        store = KrThemeStore(tmp_path / f"{suffix}.sqlite3")
        result = collect_kis_kr_rankings(
            SequenceFetcher([raw]),
            store,
            collection_cycle_id=f"{CYCLE_ID}-{suffix}",
            collection_date=COLLECTION_DATE,
            _sleeper=lambda _: None,
        )
        assert result.run.status is KrCoverageStatus.FAILED
        assert result.run.failure_code == failure_code
        assert result.receipt_count == 1
        assert result.run.record_count == 0
        assert len(store.catalysts()) == 0


def test_transport_failure_without_receipt_closes_failed_run(tmp_path: Path) -> None:
    now = RECEIVED_AT + dt.timedelta(minutes=5)
    result = collect_kis_kr_rankings(
        SequenceFetcher([KisKrRankingTransportError()]),
        KrThemeStore(tmp_path / "kr.sqlite3"),
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _clock=lambda: now,
        _sleeper=lambda _: None,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "transport_error"
    assert result.run.started_at == now
    assert result.run.completed_at == now
    assert result.receipt_count == 0
    assert result.run.record_count == 0


def test_orphan_receipt_becomes_incomplete_restart_without_network(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    raw = _raw(KisKrRankingKind.FLUCTUATION, _payload("fluctuation"))
    receipt = KrSourceReceipt(
        source_run_id=SOURCE_RUN_ID,
        source=KrCatalystSource.KIS_RANKING,
        request_key=raw.request_key,
        received_at=raw.received_at,
        http_status=raw.status_code,
        content_type=raw.content_type,
        payload_sha256=hashlib.sha256(raw.raw_payload).hexdigest(),
    )
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, raw.raw_payload)

    result = collect_kis_kr_rankings(
        RejectingFetcher(),
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "incomplete_restart"
    assert result.receipt_count == 1
    assert result.run.record_count == 0
    assert result.restarted is True
    assert len(store.source_runs()) == 1


def _payload(kind: str, *, symbol: str | None = None, rank: int | None = None) -> bytes:
    document = json.loads((FIXTURE_DIR / f"{kind}-page-1.json").read_bytes())
    if symbol is not None or rank is not None:
        row = document["output"][0]
        symbol_key = "stck_shrn_iscd" if kind == "fluctuation" else "mksc_shrn_iscd"
        if symbol is not None:
            row[symbol_key] = symbol
        if rank is not None:
            row["data_rank"] = str(rank)
    return json.dumps(document, ensure_ascii=False).encode()


def _raw(
    kind: KisKrRankingKind,
    payload: bytes,
    *,
    page_no: int = 1,
    attempt: int = 1,
    request_tr_cont: str = "",
    response_tr_cont: str = "",
    status_code: int = 200,
    received_at: dt.datetime = RECEIVED_AT,
) -> KisKrRankingRawResponse:
    return KisKrRankingRawResponse(
        kind=kind,
        page_no=page_no,
        attempt=attempt,
        request_tr_cont=request_tr_cont,
        response_tr_cont=response_tr_cont,
        request_key=(
            f"kis-kr:{kind.value}:p{page_no}:a{attempt}:"
            f"rq-{request_tr_cont.lower()}:rs-{response_tr_cont.lower()}"
        ),
        received_at=received_at,
        status_code=status_code,
        content_type="application/json",
        raw_payload=payload,
    )
