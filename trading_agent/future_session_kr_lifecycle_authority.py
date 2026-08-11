from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.kr_theme_research_chain_rollover import (
    load_kr_theme_research_rollover_bundle,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent
from trading_agent.research_identity_models import AgentOperatingMode


class InvalidKrFutureSessionLifecycleAuthorityError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR future-session lifecycle authority is invalid"


@dataclass(frozen=True, slots=True)
class KrFutureSessionLifecycleRequest:
    experiment_ledger: ExperimentLedgerStore
    calendar_store: Path
    rollover_bundle: Path
    code_version: str
    strategy_version: str
    target_session: dt.date
    decided_at: dt.datetime


@dataclass(frozen=True, slots=True)
class KrFutureSessionLifecycleResult:
    created: bool
    event: MultiMarketStrategyLifecycleEvent


def bootstrap_kr_future_session_lifecycle(
    request: KrFutureSessionLifecycleRequest,
) -> KrFutureSessionLifecycleResult:
    snapshots = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(request.calendar_store).snapshots()
        if any(
            day.session_date == request.target_session and day.open_day and day.business_day and day.trading_day
            for day in snapshot.payload.days
        )
    )
    versions = tuple(
        item.registration
        for item in request.experiment_ledger.multi_market_strategy_versions()
        if item.registration.strategy_version == request.strategy_version
    )
    if len(snapshots) != 1 or len(versions) != 1:
        raise InvalidKrFutureSessionLifecycleAuthorityError
    version = versions[0]
    bundle = load_kr_theme_research_rollover_bundle(request.rollover_bundle)
    if (
        request.decided_at.tzinfo is None
        or request.decided_at.utcoffset() is None
        or version != bundle.day_version
        or version.code_version != request.code_version
        or version.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or version.operating_mode is not AgentOperatingMode.SHADOW
    ):
        raise InvalidKrFutureSessionLifecycleAuthorityError
    hypotheses = tuple(
        item.registration
        for item in request.experiment_ledger.multi_market_hypotheses()
        if item.registration.hypothesis_id == version.hypothesis_id
    )
    if len(hypotheses) != 1:
        raise InvalidKrFutureSessionLifecycleAuthorityError
    hypothesis = hypotheses[0]
    local = request.decided_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
    if local.date() >= request.target_session:
        raise InvalidKrFutureSessionLifecycleAuthorityError
    snapshot = snapshots[0]
    event = MultiMarketStrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        strategy_lane=version.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="kr_future_session_bootstrap_v1",
        decision_session_date=local.date(),
        effective_session_date=request.target_session,
        decided_at=request.decided_at,
        session_calendar_snapshot_id=snapshot.snapshot_id,
        evidence_keys=tuple(
            sorted(
                (
                    snapshot.snapshot_id,
                    hypothesis.experiment_scope_key,
                    str(multi_market_hypothesis_registration_key(hypothesis)),
                    str(multi_market_strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )
    with request.experiment_ledger.writer() as writer:
        created = writer.append_multi_market_lifecycle_event(event)
    return KrFutureSessionLifecycleResult(created=created, event=event)


__all__ = (
    "InvalidKrFutureSessionLifecycleAuthorityError",
    "KrFutureSessionLifecycleRequest",
    "KrFutureSessionLifecycleResult",
    "bootstrap_kr_future_session_lifecycle",
)
