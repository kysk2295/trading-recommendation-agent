from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.daily_research_ledger import parse_daily_record, read_daily_ledger
from trading_agent.daily_research_models import DailyResearchRecord
from trading_agent.strategy_factory import StrategyMode


class InvalidDailyResearchRecordSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "exact daily research record와 parent ledger 계보를 확인할 수 없습니다"


@dataclass(frozen=True, slots=True)
class DailyResearchRecordSource:
    record: DailyResearchRecord
    record_path: Path
    raw_sha256: str


def load_daily_research_record_source(
    session: Path,
    session_date: dt.date,
    strategy: StrategyMode,
    experiment_scope_key: str,
) -> DailyResearchRecordSource:
    records = session / "daily_research_records"
    try:
        candidates: list[DailyResearchRecordSource] = []
        for path in sorted(records.glob("*.json")):
            raw = path.read_bytes()
            record = parse_daily_record(raw.decode("utf-8"))
            if (
                record.session_date == session_date
                and record.strategy == strategy.value
                and record.experiment_scope_key == experiment_scope_key
            ):
                candidates.append(
                    DailyResearchRecordSource(
                        record=record,
                        record_path=path,
                        raw_sha256=hashlib.sha256(raw).hexdigest(),
                    )
                )
        if not candidates:
            raise InvalidDailyResearchRecordSourceError
        selected = max(
            candidates,
            key=lambda item: (item.record.recorded_at, item.record.record_id),
        )
        parent = read_daily_ledger(session.parent / "daily_research_ledger.jsonl")
    except (OSError, UnicodeError, ValidationError) as error:
        raise InvalidDailyResearchRecordSourceError from error
    if not any(record.record_id == selected.record.record_id for record in parent):
        raise InvalidDailyResearchRecordSourceError
    return selected
