from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.paper_execution_models import BrokerEventKey, IntentId


class WriterLeaseUnavailableError(RuntimeError):
    __slots__ = ("lock_path",)

    def __init__(self, lock_path: Path) -> None:
        super().__init__()
        self.lock_path = lock_path

    @override
    def __str__(self) -> str:
        return f"Paper execution writer가 이미 실행 중입니다: {self.lock_path}"


class InactiveExecutionWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Paper execution writer 사용 구간이 종료되었습니다"


class IntentConflictError(RuntimeError):
    __slots__ = ("intent_id",)

    def __init__(self, intent_id: IntentId) -> None:
        super().__init__()
        self.intent_id = intent_id

    @override
    def __str__(self) -> str:
        return f"같은 intent ID의 immutable 필드가 다릅니다: {self.intent_id}"


class BrokerEventConflictError(RuntimeError):
    __slots__ = ("event_key",)

    def __init__(self, event_key: BrokerEventKey) -> None:
        super().__init__()
        self.event_key = event_key

    @override
    def __str__(self) -> str:
        return f"같은 broker event key의 immutable 필드가 다릅니다: {self.event_key}"
