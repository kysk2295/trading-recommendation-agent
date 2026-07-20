from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol

from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentRun,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.sec_filing_document_store import SecFilingDocumentStore


class SecFilingDocumentFetcher(Protocol):
    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse: ...


def collect_sec_filing_document(
    fetcher: SecFilingDocumentFetcher,
    store: SecFilingDocumentStore,
    target: SecFilingDocumentTarget,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> SecFilingDocumentRun:
    existing = store.run_for_target(target.target_id) if _store_exists(store) else None
    if existing is not None:
        if existing.target != target:
            raise ValueError("SEC filing document replay target mismatch")
        return existing
    store.preflight_write()
    started_at = _clock()
    stored = store.receipt_for_target(target.target_id)
    if stored is None:
        try:
            response = fetcher.fetch(target)
        except Exception:
            run = _run(target, started_at, _clock(), None)
            _ = store.append_run(run)
            return run
        _ = store.append_receipt(target, response)
    else:
        if stored.target != target:
            raise ValueError("SEC filing document replay target mismatch")
        response = stored.response
    run = _run(target, started_at, _clock(), response)
    _ = store.append_run(run)
    return run


def collect_sec_filing_documents(
    fetcher: SecFilingDocumentFetcher,
    store: SecFilingDocumentStore,
    targets: tuple[SecFilingDocumentTarget, ...],
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> tuple[SecFilingDocumentRun, ...]:
    if len(targets) > 8 or len({target.target_id for target in targets}) != len(targets):
        raise ValueError("SEC filing document batch is invalid")
    runs: list[SecFilingDocumentRun] = []
    for target in targets:
        run = collect_sec_filing_document(fetcher, store, target, _clock=_clock)
        runs.append(run)
        if run.status is SecFilingDocumentStatus.FAILED:
            break
    return tuple(runs)


def _run(
    target: SecFilingDocumentTarget,
    started_at: dt.datetime,
    completed_at: dt.datetime,
    response: SecFilingDocumentRawResponse | None,
) -> SecFilingDocumentRun:
    if response is None:
        status = SecFilingDocumentStatus.FAILED
        failure_code = "transport"
        receipt_id = None
        byte_count = 0
        run_started_at = min(started_at, completed_at)
        run_completed_at = max(started_at, completed_at)
    else:
        failure_code = (
            "http_status" if response.status_code != 200 else "empty_payload" if not response.raw_payload else None
        )
        status = SecFilingDocumentStatus.SUCCESS if failure_code is None else SecFilingDocumentStatus.FAILED
        receipt_id = response.receipt_id
        byte_count = len(response.raw_payload)
        run_started_at = min(started_at, response.received_at)
        run_completed_at = max(completed_at, started_at, response.received_at)
    return SecFilingDocumentRun(
        target=target,
        started_at=run_started_at,
        completed_at=run_completed_at,
        status=status,
        failure_code=failure_code,
        receipt_id=receipt_id,
        byte_count=byte_count,
    )


def _store_exists(store: SecFilingDocumentStore) -> bool:
    return store.path.exists()
