from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.sec_edgar_client import SecEdgarTransportError
from trading_agent.sec_edgar_collection import collect_sec_submissions
from trading_agent.sec_edgar_models import SecCollectionStatus, SecSubmissionRawResponse
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store import SecEdgarStore

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)


class StubFetcher:
    """Mutable in-memory provider fake that records boundary calls."""

    __slots__ = ("calls", "failure", "response")

    def __init__(
        self,
        response: SecSubmissionRawResponse | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        self.calls.append((collection_id, cik))
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


def test_sec_collection_commits_raw_before_parse_and_replays_without_fetch(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response()
    fetcher = StubFetcher(response)

    def parser(raw: SecSubmissionRawResponse):
        assert store.receipt_for_collection(raw.collection_id, raw.cik) is not None
        return parse_sec_submission_snapshot(raw)

    first = collect_sec_submissions(fetcher, store, "sec-cycle-001", response.cik, _parser=parser)
    reject = StubFetcher(failure=AssertionError("provider called during replay"))
    replay = collect_sec_submissions(reject, store, "sec-cycle-001", response.cik)

    assert first.run.status is SecCollectionStatus.SUCCESS
    assert first.filing_count == 2
    assert first.new_filing_version_count == 2
    assert first.replayed is False
    assert replay.run == first.run
    assert replay.replayed is True
    assert reject.calls == []


def test_sec_collection_preserves_http_error_raw_and_terminal_failure(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = SecSubmissionRawResponse(
        collection_id="sec-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=403,
        content_type="text/html",
        raw_payload=b"private provider response",
    )

    result = collect_sec_submissions(StubFetcher(response), store, response.collection_id, response.cik)

    stored = store.receipt_for_collection(response.collection_id, response.cik)
    assert stored is not None
    assert stored.response.raw_payload == response.raw_payload
    assert result.run.status is SecCollectionStatus.FAILED
    assert result.run.failure_code == "http_403"
    assert result.filing_count == 0


def test_sec_collection_preserves_empty_http_error_raw_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = SecSubmissionRawResponse(
        collection_id="sec-cycle-empty",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=503,
        content_type="text/plain",
        raw_payload=b"",
    )

    result = collect_sec_submissions(StubFetcher(response), store, response.collection_id, response.cik)

    stored = store.receipt_for_collection(response.collection_id, response.cik)
    assert stored is not None
    assert stored.response.raw_payload == b""
    assert result.run.failure_code == "http_503"


def test_sec_collection_persists_transport_failure_without_receipt(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")

    result = collect_sec_submissions(
        StubFetcher(failure=SecEdgarTransportError()),
        store,
        "sec-cycle-001",
        "0000320193",
        _clock=lambda: RECEIVED_AT,
    )

    assert result.run.status is SecCollectionStatus.FAILED
    assert result.run.failure_code == "transport"
    assert result.run.receipt_id is None
    assert store.receipt_for_collection("sec-cycle-001", "0000320193") is None


def test_sec_collection_recovers_orphan_receipt_without_provider(tmp_path: Path) -> None:
    store = SecEdgarStore(tmp_path / "sec.sqlite3")
    response = _response()
    _ = store.append_receipt(response)
    reject = StubFetcher(failure=AssertionError("provider called for orphan receipt"))

    result = collect_sec_submissions(reject, store, response.collection_id, response.cik)

    assert reject.calls == []
    assert result.run.status is SecCollectionStatus.SUCCESS
    assert result.receipt_created is False
    assert result.new_filing_version_count == 2


def test_sec_collection_rejects_invalid_store_path_before_provider(tmp_path: Path) -> None:
    link = tmp_path / "sec.sqlite3"
    link.symlink_to(tmp_path / "missing-target.sqlite3")
    fetcher = StubFetcher(_response())

    with pytest.raises(ValueError):
        _ = collect_sec_submissions(fetcher, SecEdgarStore(link), "sec-cycle-001", "0000320193")

    assert fetcher.calls == []


def test_sec_collection_rejects_oversized_typed_response_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "sec.sqlite3"
    store = SecEdgarStore(database)
    store.preflight_write()
    before = database.read_bytes()
    response = _response()
    object.__setattr__(response, "raw_payload", b"x" * (64 * 1024 * 1024 + 1))

    with pytest.raises(ValueError):
        _ = collect_sec_submissions(
            StubFetcher(response),
            store,
            response.collection_id,
            response.cik,
        )

    assert database.read_bytes() == before
    assert store.receipt_for_collection(response.collection_id, response.cik) is None


def _response() -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id="sec-cycle-001",
        cik="0000320193",
        received_at=RECEIVED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=FIXTURE.read_bytes(),
    )
