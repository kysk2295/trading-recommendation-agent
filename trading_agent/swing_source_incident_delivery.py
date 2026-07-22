from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Final, override

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_swing_operating_models import SwingScanFailed

_SWING_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SWING_TRADING,
    strategy_id=SWING_RESEARCH_CONTRACT.strategy_id,
)


class InvalidSwingSourceIncidentError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing source incident projection is invalid"


def project_swing_source_incident(
    session_date: dt.date,
    failure: SwingScanFailed,
    delivery_store: HermesDeliveryStore,
) -> HermesProjectionResult:
    try:
        bounds = regular_session_bounds(session_date)
        if (
            bounds is None
            or failure.failed_at.tzinfo is None
            or failure.failed_at.utcoffset() is None
            or failure.failed_at.astimezone(NEW_YORK).date() != session_date
            or failure.failed_at < bounds[1]
        ):
            raise InvalidSwingSourceIncidentError
        material = (
            session_date.isoformat(),
            failure.reason.value,
            _SWING_LANE.canonical_id,
            SWING_RESEARCH_CONTRACT.strategy_version,
        )
        digest = hashlib.sha256(json.dumps(material, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        record = HermesProjectionRecord(
            source_event_id=(
                f"swing-source-incident:{session_date:%Y%m%d}:"
                f"{SWING_RESEARCH_CONTRACT.strategy_version}:{failure.reason.value}"
            ),
            root_source_event_id=None,
            kind=HermesDeliveryKind.INCIDENT,
            market_id=_SWING_LANE.market_id.value,
            agent_family=_SWING_LANE.agent_family.value,
            lane_id=_SWING_LANE.canonical_id,
            strategy_version=SWING_RESEARCH_CONTRACT.strategy_version,
            instrument_id=None,
            occurred_at=bounds[1],
            status="blocked_source_unavailable",
            evidence_refs=(f"swing-source-cycle:{session_date.isoformat()}",),
            rendered_text=(
                "US Swing 장후 데이터 수집 실패: 오늘 추천을 차단했습니다. "
                "소스가 복구된 뒤 같은 세션을 다시 실행할 수 있으며 계좌 주문은 없습니다."
            ),
            payload_sha256=digest,
        )
        with delivery_store.writer() as writer:
            return project_outcomes((record,), writer)
    except InvalidSwingSourceIncidentError:
        raise
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidSwingSourceIncidentError from None


__all__ = (
    "InvalidSwingSourceIncidentError",
    "project_swing_source_incident",
)
