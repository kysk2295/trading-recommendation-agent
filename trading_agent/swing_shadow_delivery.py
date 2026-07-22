from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Final, override

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    project_outcomes,
    project_trade_signals,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore, HermesDeliveryWriter
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowEvent, SwingShadowReader
from trading_agent.swing_shadow_trial import (
    swing_shadow_trial_artifact_sha256s,
    swing_shadow_trial_id,
)

_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.EXPIRED,
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
    }
)
_SWING_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SWING_TRADING,
    strategy_id=SWING_RESEARCH_CONTRACT.strategy_id,
)


class InvalidSwingShadowDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow Hermes delivery source is invalid"


def project_swing_shadow_cycle_delivery(
    source: SwingDailySource,
    signals: tuple[TradeSignalEnvelope, ...],
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    try:
        source = SwingDailySource.model_validate(source.model_dump(mode="python"))
        expected = project_new_high_rvol_signals(source)
        validated = tuple(
            TradeSignalEnvelope.model_validate(signal.model_dump(mode="python")) for signal in signals
        )
        if validated != expected:
            raise InvalidSwingShadowDeliveryError
        if validated:
            return project_trade_signals(validated, writer, frozenset())
        return project_outcomes((_no_recommendation_record(source),), writer)
    except InvalidSwingShadowDeliveryError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise InvalidSwingShadowDeliveryError from None


def project_swing_shadow_terminal_delivery(
    experiment_ledger: ExperimentLedgerReader,
    shadow_ledger: SwingShadowReader,
    delivery_store: HermesDeliveryStore,
    signal_id: str,
) -> HermesProjectionResult:
    try:
        signal, terminal, terminal_event_key = _verified_terminal(
            experiment_ledger,
            shadow_ledger,
            signal_id,
        )
        _require_watch(delivery_store, signal)
        record = _terminal_record(signal, terminal, terminal_event_key)
        with delivery_store.writer() as writer:
            return project_outcomes((record,), writer)
    except InvalidSwingShadowDeliveryError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise InvalidSwingShadowDeliveryError from None


def _verified_terminal(
    experiment_ledger: ExperimentLedgerReader,
    shadow_ledger: SwingShadowReader,
    signal_id: str,
) -> tuple[TradeSignalEnvelope, SwingShadowEvent, str]:
    if not signal_id or signal_id != signal_id.strip():
        raise InvalidSwingShadowDeliveryError
    signals = tuple(signal for signal in shadow_ledger.signals() if signal.signal_id == signal_id)
    if len(signals) != 1:
        raise InvalidSwingShadowDeliveryError
    signal = signals[0]
    shadow_events = shadow_ledger.events(signal_id)
    artifacts = swing_shadow_trial_artifact_sha256s(signal, shadow_events)
    terminal = shadow_events[-1]
    trial_id = swing_shadow_trial_id(signal)
    trials = tuple(item for item in experiment_ledger.trials() if item.registration.trial_id == trial_id)
    trial_events = experiment_ledger.trial_events(trial_id)
    if (
        terminal.kind not in _TERMINAL_KINDS
        or len(trials) != 1
        or trials[0].registration.strategy_version != signal.producer_strategy_version
        or len(trial_events) != 2
        or trial_events[0].event.event_kind is not TrialEventKind.STARTED
        or trial_events[1].event.event_kind is not TrialEventKind.COMPLETED
        or trial_events[1].event.artifact_sha256s != artifacts
        or trial_events[1].event.occurred_at < terminal.observed_at
    ):
        raise InvalidSwingShadowDeliveryError
    return signal, terminal, str(trial_events[1].event_key)


def _require_watch(store: HermesDeliveryStore, signal: TradeSignalEnvelope) -> None:
    matches = tuple(event for event in store.events() if event.source_event_id == signal.signal_id)
    if (
        len(matches) != 1
        or matches[0].kind is not HermesDeliveryKind.WATCH
        or matches[0].root_delivery_id != matches[0].delivery_id
        or matches[0].market_id != signal.strategy_lane.market_id.value
        or matches[0].agent_family != signal.strategy_lane.agent_family.value
        or matches[0].lane_id != signal.strategy_lane.canonical_id
        or matches[0].strategy_version != signal.producer_strategy_version
        or matches[0].instrument_id != signal.symbol
        or matches[0].payload_sha256 != _sha256(signal)
    ):
        raise InvalidSwingShadowDeliveryError


def _no_recommendation_record(source: SwingDailySource) -> HermesProjectionRecord:
    return HermesProjectionRecord(
        source_event_id=f"swing-cycle:{source.session_date:%Y%m%d}:{source.source_key[:16]}",
        root_source_event_id=None,
        kind=HermesDeliveryKind.NO_RECOMMENDATION,
        market_id=_SWING_LANE.market_id.value,
        agent_family=_SWING_LANE.agent_family.value,
        lane_id=_SWING_LANE.canonical_id,
        strategy_version=SWING_RESEARCH_CONTRACT.strategy_version,
        instrument_id=None,
        occurred_at=source.observed_at,
        status="no_setup",
        evidence_refs=(f"swing-source:{source.source_key}",),
        rendered_text=(
            "US Swing 장후 결과: 추천 없음\n"
            "- 완료 일봉 기준 신고가·상대거래량 조건을 충족한 종목이 없습니다.\n"
            "- 조건부 shadow 연구이며 계좌 주문은 없습니다."
        ),
        payload_sha256=_sha256(source),
    )


def _terminal_record(
    signal: TradeSignalEnvelope,
    terminal: SwingShadowEvent,
    terminal_event_key: str,
) -> HermesProjectionRecord:
    kind = (
        HermesDeliveryKind.NO_RECOMMENDATION
        if terminal.kind is ShadowEventKind.EXPIRED
        else HermesDeliveryKind.EXIT
    )
    rendered = (
        "US Swing 조건부 신호 미체결 만료\n"
        f"- 종목: {signal.symbol}\n"
        "- 다음 정규장 trigger가 충족되지 않아 진입하지 않았습니다.\n"
        "- 계좌 주문 없음"
        if terminal.kind is ShadowEventKind.EXPIRED
        else (
            "US Swing shadow 종료\n"
            f"- 종목: {signal.symbol}\n"
            f"- 종료 사유: {terminal.kind.value}\n"
            f"- shadow 종료가: {_price(terminal.price)}\n"
            "- 계좌 주문 없음"
        )
    )
    return HermesProjectionRecord(
        source_event_id=f"swing-terminal:{_sha256(terminal)}",
        root_source_event_id=signal.signal_id,
        kind=kind,
        market_id=signal.strategy_lane.market_id.value,
        agent_family=signal.strategy_lane.agent_family.value,
        lane_id=signal.strategy_lane.canonical_id,
        strategy_version=signal.producer_strategy_version,
        instrument_id=signal.symbol,
        occurred_at=terminal.observed_at,
        status=terminal.kind.value,
        evidence_refs=tuple(
            sorted(
                (
                    f"shadow-event:{_sha256(terminal)}",
                    f"trial-event:{terminal_event_key}",
                )
            )
        ),
        rendered_text=rendered,
        payload_sha256=_sha256(terminal),
    )


def _sha256(value: SwingDailySource | TradeSignalEnvelope | SwingShadowEvent) -> str:
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


def _price(value: Decimal | None) -> str:
    if value is None:
        raise InvalidSwingShadowDeliveryError
    return format(value.normalize(), "f")


__all__ = (
    "InvalidSwingShadowDeliveryError",
    "project_swing_shadow_cycle_delivery",
    "project_swing_shadow_terminal_delivery",
)
