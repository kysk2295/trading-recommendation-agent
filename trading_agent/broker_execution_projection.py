from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.broker_order_evidence import BrokerOrderEvidence
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_account_activity_projection import (
    AVERAGE_PRICE_TOLERANCE,
    project_paper_activity_execution,
)
from trading_agent.paper_execution_models import BrokerOrderEventType
from trading_agent.paper_stream_recovery import StoredPaperRecoveryOrder
from trading_agent.trade_update_schema import StoredTradeUpdate

EXECUTION_EVENT_TYPES = frozenset((BrokerOrderEventType.PARTIAL_FILL, BrokerOrderEventType.FILL))


@dataclass(frozen=True, slots=True)
class BrokerExecutionProjection:
    ordered_updates: tuple[StoredTradeUpdate, ...]
    cumulative_quantity: Decimal
    average_price: Decimal | None
    detail_complete: bool
    complete_fill: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def project_broker_execution(
    intent: StoredIntent,
    evidence: BrokerOrderEvidence,
    latest_recovery: StoredPaperRecoveryOrder | None,
) -> BrokerExecutionProjection:
    ordered_updates = tuple(
        sorted(
            evidence.trade_updates,
            key=lambda event: (_instant(event.occurred_at), event.event_id),
        )
    )
    executions = tuple(event for event in ordered_updates if event.event_type in EXECUTION_EVENT_TYPES)
    stream_reasons: list[str] = []
    stream_quantity = Decimal(0)
    stream_notional = Decimal(0)
    for execution in executions:
        if execution.execution_id is None:
            stream_reasons.append("체결 event에 execution ID가 없습니다")
        if execution.execution_quantity is None:
            stream_reasons.append("체결 event에 개별 체결 수량이 없습니다")
            continue
        stream_quantity += execution.execution_quantity
        if execution.execution_price is None:
            stream_reasons.append("체결 event에 개별 체결 가격이 없습니다")
        else:
            stream_notional += execution.execution_quantity * execution.execution_price
        if execution.cumulative_filled_quantity != stream_quantity:
            stream_reasons.append("체결 event 사이에 누락 또는 누적 수량 불일치가 있습니다")
    stream_cumulative = max(
        (event.cumulative_filled_quantity for event in evidence.trade_updates),
        default=Decimal(0),
    )
    if stream_cumulative > 0 and stream_quantity != stream_cumulative:
        stream_reasons.append("체결 event 누락 또는 최종 누적 수량 불일치가 있습니다")
    stream_complete = not stream_reasons
    stream_average = stream_notional / stream_quantity if stream_quantity > 0 and stream_complete else None
    cumulative = stream_cumulative
    average = stream_average
    detail_complete = stream_complete
    reasons: list[str] = []
    warnings: list[str] = []
    activities_by_id = {item.activity.activity_id: item.activity for item in evidence.account_activities}
    activities = tuple(activities_by_id.values())
    activity_projection = (
        None
        if latest_recovery is None or not activities
        else project_paper_activity_execution(latest_recovery.order, activities)
    )
    activity_complete = activity_projection is not None and activity_projection.complete
    if activity_projection is not None and not activity_projection.complete:
        reasons.extend(activity_projection.reasons)
    if latest_recovery is not None:
        recovered = latest_recovery.order
        if recovered.filled_quantity < stream_cumulative:
            reasons.append("REST 누적 체결 수량이 trade update 원장보다 작습니다")
        if recovered.filled_quantity > stream_cumulative:
            cumulative = recovered.filled_quantity
        if activity_complete and activity_projection is not None:
            cumulative = activity_projection.cumulative_quantity
            average = activity_projection.average_price
            detail_complete = True
            if stream_reasons or stream_cumulative != cumulative:
                warnings.append("WSS 체결 누락을 Account Activities로 복구했습니다")
            if (
                stream_average is not None
                and average is not None
                and abs(stream_average - average) > AVERAGE_PRICE_TOLERANCE
            ):
                reasons.append("WSS 체결 평균가격과 Account Activities가 다릅니다")
                detail_complete = False
        elif recovered.filled_quantity > stream_cumulative:
            detail_complete = False
            warnings.append("REST 누적 체결은 개별 execution 상세가 불완전합니다")
        if (
            recovered.filled_quantity > 0
            and recovered.filled_quantity == stream_quantity
            and stream_complete
            and recovered.filled_average_price is not None
            and stream_average is not None
            and abs(recovered.filled_average_price - stream_average) > AVERAGE_PRICE_TOLERANCE
        ):
            reasons.append("REST 누적 체결 평균가격과 개별 execution이 불일치합니다")
            detail_complete = False
    if stream_reasons and not activity_complete:
        reasons.extend(stream_reasons)
        detail_complete = False
    complete_fill = any(
        event.event_type is BrokerOrderEventType.FILL and event.cumulative_filled_quantity == Decimal(intent.quantity)
        for event in evidence.trade_updates
    )
    if latest_recovery is not None:
        recovered = latest_recovery.order
        complete_fill = complete_fill or (
            recovered.status == "filled"
            and recovered.quantity == Decimal(intent.quantity)
            and recovered.filled_quantity == Decimal(intent.quantity)
        )
    if any(event.event_type is BrokerOrderEventType.FILL for event in evidence.trade_updates) and not complete_fill:
        reasons.append("완전체결 event의 누적 수량이 주문 수량과 다릅니다")
    return BrokerExecutionProjection(
        ordered_updates=ordered_updates,
        cumulative_quantity=cumulative,
        average_price=average,
        detail_complete=detail_complete,
        complete_fill=complete_fill,
        reasons=tuple(sorted(set(reasons))),
        warnings=tuple(sorted(set(warnings))),
    )


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise StoredBrokerExecutionTimestampError
    return instant


class StoredBrokerExecutionTimestampError(RuntimeError):
    pass
