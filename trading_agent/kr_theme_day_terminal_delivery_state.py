from __future__ import annotations

from typing import assert_never

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_day_trial_terminal_models import (
    KrThemeDayTrialTerminalArtifact,
    KrThemeDayTrialTerminalReason,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE


class InvalidKrThemeDayTerminalDeliveryStateError(ValueError):
    pass


def kr_theme_day_terminal_delivery_references(
    store: HermesDeliveryStore,
    artifact: KrThemeDayTrialTerminalArtifact,
) -> tuple[str, ...]:
    terminal_reference = f"terminal:{artifact.artifact_id}"
    events = tuple(event for event in store.events() if terminal_reference in event.evidence_refs)
    expected_kind, expected_sources = _expected_delivery(artifact)
    lane = KR_THEME_LEADER_VWAP_RECLAIM_LANE
    if (
        len(events) != len(expected_sources)
        or {event.source_event_id for event in events} != expected_sources
        or any(
            event.kind is not expected_kind
            or event.market_id != lane.market_id.value
            or event.agent_family != lane.agent_family.value
            or event.lane_id != lane.canonical_id
            or event.strategy_version != artifact.payload.strategy_version
            for event in events
        )
    ):
        raise InvalidKrThemeDayTerminalDeliveryStateError
    return tuple(f"delivery:{event.delivery_id}" for event in sorted(events, key=lambda item: item.delivery_id))


def _expected_delivery(
    artifact: KrThemeDayTrialTerminalArtifact,
) -> tuple[HermesDeliveryKind, set[str]]:
    match artifact.payload.terminal_kind:
        case TrialEventKind.COMPLETED:
            return HermesDeliveryKind.EXIT, {f"kr-exit:{value}" for value in artifact.payload.exit_ids}
        case TrialEventKind.CENSORED:
            no_entry = (KrThemeDayTrialTerminalReason.NO_SHADOW_ENTRY_ARTIFACT.value,)
            kind = (
                HermesDeliveryKind.NO_RECOMMENDATION
                if artifact.payload.reason_codes == no_entry
                else HermesDeliveryKind.INCIDENT
            )
            return kind, {f"kr-terminal:{artifact.artifact_id}"}
        case TrialEventKind.FAILED:
            return HermesDeliveryKind.INCIDENT, {f"kr-terminal:{artifact.artifact_id}"}
        case TrialEventKind.STARTED:
            raise InvalidKrThemeDayTerminalDeliveryStateError
        case unreachable:
            assert_never(unreachable)


__all__ = ("kr_theme_day_terminal_delivery_references",)
