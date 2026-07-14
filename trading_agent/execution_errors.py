from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.paper_execution_models import BrokerEventKey, IntentId


class AccountBindingConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "실행 원장이 다른 Alpaca paper 계좌에 이미 결합되어 있습니다"


class UnboundExecutionAccountError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "실행 원장이 Alpaca paper 계좌에 결합되지 않았습니다"


class UnknownTradeUpdateIntentError(RuntimeError):
    __slots__ = ("intent_id",)

    def __init__(self, intent_id: IntentId) -> None:
        super().__init__()
        self.intent_id = intent_id

    @override
    def __str__(self) -> str:
        return f"trade_updates의 intent가 실행 원장에 없습니다: {self.intent_id}"


class InvalidTradeUpdateReceiptError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "trade_updates 수신 세대와 수신시각이 올바르지 않습니다"


class TradeUpdateConflictError(RuntimeError):
    __slots__ = ("event_key",)

    def __init__(self, event_key: BrokerEventKey) -> None:
        super().__init__()
        self.event_key = event_key

    @override
    def __str__(self) -> str:
        return f"같은 trade update key의 immutable 필드가 다릅니다: {self.event_key}"


class TradeUpdateOrderMismatchError(RuntimeError):
    __slots__ = ("intent_id", "mismatches")

    def __init__(self, intent_id: IntentId, mismatches: tuple[str, ...]) -> None:
        super().__init__()
        self.intent_id = intent_id
        self.mismatches = mismatches

    @override
    def __str__(self) -> str:
        fields = ", ".join(self.mismatches)
        return f"trade_updates 주문과 저장된 intent가 불일치합니다: {self.intent_id} ({fields})"


class UnexpectedBrokerOrderIdError(RuntimeError):
    __slots__ = ("broker_order_id", "intent_id")

    def __init__(self, intent_id: IntentId, broker_order_id: str) -> None:
        super().__init__()
        self.intent_id = intent_id
        self.broker_order_id = broker_order_id

    @override
    def __str__(self) -> str:
        return f"연결되지 않은 두 번째 broker order ID입니다: {self.intent_id} ({self.broker_order_id})"


class UnsupportedExecutionSchemaError(RuntimeError):
    __slots__ = ("path", "version")

    def __init__(self, path: Path, version: int) -> None:
        super().__init__()
        self.path = path
        self.version = version

    @override
    def __str__(self) -> str:
        return f"지원하지 않는 execution 원장 스키마입니다: v{self.version} ({self.path})"


class ExecutionSchemaIntegrityError(RuntimeError):
    __slots__ = ("missing_objects", "path")

    def __init__(self, path: Path, missing_objects: tuple[str, ...]) -> None:
        super().__init__()
        self.path = path
        self.missing_objects = missing_objects

    @override
    def __str__(self) -> str:
        invalid = ", ".join(self.missing_objects)
        return f"execution 원장 스키마 무결성 검사에 실패했습니다: {invalid} ({self.path})"
