from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from bisect import bisect_right

from trading_agent.challenger_replay_models import (
    ReplayBar,
    ReplayContext,
    ReplaySource,
    ReplaySourceRejectedError,
)
from trading_agent.challenger_replay_source import load_replay_source
from trading_agent.intraday_research_dataset_models import (
    IntradayResearchDatasetError,
    IntradayResearchDatasetReceipt,
    IntradayResearchDatasetRequest,
    IntradayResearchDatasetResult,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

_CSV_HEADER = (
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "prior_close",
    "average_daily_volume",
    "spread_bps",
    "catalyst",
)


def materialize_intraday_research_dataset(
    request: IntradayResearchDatasetRequest,
) -> IntradayResearchDatasetResult:
    if (
        not request.session_dirs
        or request.max_sessions < 1
        or request.max_sessions > 60
        or request.max_bars < 1
        or request.max_bars > 100_000
        or len(request.session_dirs) > request.max_sessions
    ):
        raise IntradayResearchDatasetError("invalid_budget")
    try:
        sources = tuple(load_replay_source(path) for path in request.session_dirs)
        session_dates = tuple(source.session_date for source in sources)
        if len(set(session_dates)) != len(session_dates):
            raise IntradayResearchDatasetError("duplicate_session_date")
        ordered = tuple(source for _, source in sorted(zip(session_dates, sources, strict=True)))
        csv_payload, eligible, censored, bar_count = _dataset_csv(ordered, request.max_bars)
        input_sha256 = hashlib.sha256(csv_payload.encode()).hexdigest()
        source_hashes = tuple(_source_sha256(source) for source in ordered)
        receipt = IntradayResearchDatasetReceipt(
            input_sha256=input_sha256,
            source_session_sha256s=source_hashes,
            session_dates=tuple(source.session_date for source in ordered),
            eligible_symbol_sessions=eligible,
            censored_symbol_sessions=censored,
            bar_count=bar_count,
        )
        receipt_payload = (
            json.dumps(
                receipt.model_dump(mode="json"),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        receipt_sha256 = hashlib.sha256(receipt_payload.encode()).hexdigest()
        stem = f"intraday_point_in_time_{input_sha256}"
        csv_path = request.output_root / f"{stem}.csv"
        receipt_path = request.output_root / f"{stem}_{receipt_sha256}.json"
        csv_created = publish_private_immutable_text(csv_path, csv_payload)
        receipt_created = publish_private_immutable_text(
            receipt_path,
            receipt_payload,
        )
    except IntradayResearchDatasetError:
        raise
    except (InvalidPrivateImmutableFileError, ReplaySourceRejectedError, TypeError, ValueError):
        raise IntradayResearchDatasetError("source_or_publication_invalid") from None
    return IntradayResearchDatasetResult(
        csv_path=csv_path,
        receipt_path=receipt_path,
        input_sha256=input_sha256,
        source_session_sha256s=source_hashes,
        session_count=len(ordered),
        eligible_symbol_sessions=eligible,
        censored_symbol_sessions=censored,
        bar_count=bar_count,
        created=csv_created or receipt_created,
    )


def _dataset_csv(
    sources: tuple[ReplaySource, ...],
    max_bars: int,
) -> tuple[str, int, int, int]:
    handle = io.StringIO(newline="")
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    eligible = 0
    censored = 0
    bar_count = 0
    for source in sources:
        complete_keys = {
            (coverage.exchange, coverage.symbol)
            for coverage in source.coverage
            if coverage.complete
        }
        contexts_by_key = _contexts_by_key(source.contexts)
        bars_by_key: dict[tuple[str, str], int] = {}
        seen_bars: set[tuple[str, str, dt.datetime]] = set()
        for bar in source.bars:
            key = (bar.exchange, bar.symbol)
            identity = (*key, bar.timestamp)
            if identity in seen_bars:
                raise IntradayResearchDatasetError("duplicate_candidate_bar")
            seen_bars.add(identity)
            contexts = contexts_by_key.get(key, ())
            observed_times = tuple(row.observed_at for row in contexts)
            context_index = bisect_right(observed_times, bar.timestamp) - 1
            if key not in complete_keys or context_index < 0:
                continue
            context = contexts[context_index]
            writer.writerow(_csv_row(bar, context))
            bars_by_key[key] = bars_by_key.get(key, 0) + 1
            bar_count += 1
            if bar_count > max_bars:
                raise IntradayResearchDatasetError("bar_budget_exceeded")
        eligible += len(bars_by_key)
        censored += len(source.coverage) - len(bars_by_key)
    if bar_count == 0:
        raise IntradayResearchDatasetError("no_causally_eligible_bars")
    return handle.getvalue(), eligible, censored, bar_count


def _contexts_by_key(
    contexts: tuple[ReplayContext, ...],
) -> dict[tuple[str, str], tuple[ReplayContext, ...]]:
    grouped: dict[tuple[str, str], list[ReplayContext]] = {}
    for context in contexts:
        grouped.setdefault((context.exchange, context.symbol), []).append(context)
    return {
        key: tuple(sorted(rows, key=lambda row: row.observed_at))
        for key, rows in grouped.items()
    }


def _csv_row(
    bar: ReplayBar,
    context: ReplayContext,
) -> tuple[str | int | float, ...]:
    return (
        bar.timestamp.isoformat(),
        bar.symbol,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
        context.prior_close,
        context.average_daily_volume,
        context.spread_bps,
        "",
    )


def _source_sha256(source: ReplaySource) -> str:
    payload = {
        "bars": [
            {
                "exchange": row.exchange,
                "symbol": row.symbol,
                "timestamp": row.timestamp.isoformat(),
                "first_observed_at": row.first_observed_at.isoformat(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in source.bars
        ],
        "contexts": [
            {
                "exchange": row.exchange,
                "symbol": row.symbol,
                "observed_at": row.observed_at.isoformat(),
                "latest_completed_bar_at": row.latest_completed_bar_at.isoformat(),
                "prior_close": row.prior_close,
                "average_daily_volume": row.average_daily_volume,
                "spread_bps": row.spread_bps,
            }
            for row in source.contexts
        ],
        "coverage": [
            {
                "exchange": row.exchange,
                "symbol": row.symbol,
                "expected_minutes": row.expected_minutes,
                "archived_minutes": row.archived_minutes,
                "complete": row.complete,
                "reason": row.reason,
            }
            for row in source.coverage
        ],
        "session_date": source.session_date.isoformat(),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "IntradayResearchDatasetError",
    "IntradayResearchDatasetReceipt",
    "IntradayResearchDatasetRequest",
    "IntradayResearchDatasetResult",
    "materialize_intraday_research_dataset",
)
