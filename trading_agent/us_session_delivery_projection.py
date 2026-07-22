from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo

from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    HermesProjectionSources,
    InvalidHermesProjectionSourceError,
    opportunity_projection_records,
    project_outcomes,
    read_opportunity_snapshots,
    signal_publication_projection_record,
)
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.trade_signal_outbox_reader import (
    TradeSignalOutboxReaderError,
    read_trade_signal_publications,
)
from trading_agent.trade_signal_publication import TradeSignalPublication

_NEW_YORK = ZoneInfo("America/New_York")


def project_us_session_contract_outboxes(
    sources: HermesProjectionSources,
    session_date: dt.date,
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    return project_outcomes(
        us_session_projection_records(sources, session_date),
        writer,
    )


def us_session_projection_records(
    sources: HermesProjectionSources,
    session_date: dt.date,
) -> tuple[HermesProjectionRecord, ...]:
    snapshots = tuple(
        snapshot
        for snapshot in read_opportunity_snapshots(sources.opportunity_outbox)
        if snapshot.observed_at.astimezone(_NEW_YORK).date() == session_date
    )
    try:
        publications = tuple(
            publication
            for publication in read_trade_signal_publications(sources.signal_outbox)
            if publication.signal.observed_at.astimezone(_NEW_YORK).date()
            == session_date
        )
    except TradeSignalOutboxReaderError:
        raise InvalidHermesProjectionSourceError from None
    roots = _first_symbol_roots(snapshots)
    signals = _signal_records(publications, snapshots, roots)
    return (*roots.values(), *signals)


def us_session_projection_sha256(
    records: tuple[HermesProjectionRecord, ...],
) -> str:
    validated = tuple(
        HermesProjectionRecord.model_validate(record.model_dump(mode="python"))
        for record in records
    )
    ordered = tuple(sorted(validated, key=lambda item: item.source_event_id))
    return hashlib.sha256(
        b"\n".join(record.model_dump_json().encode() for record in ordered)
    ).hexdigest()


def _first_symbol_roots(
    snapshots: tuple[OpportunitySnapshot, ...],
) -> dict[str, HermesProjectionRecord]:
    roots: dict[str, HermesProjectionRecord] = {}
    for snapshot in sorted(
        snapshots,
        key=lambda item: (item.observed_at, item.opportunity_id),
    ):
        for record in opportunity_projection_records(snapshot):
            if record.instrument_id is not None and record.instrument_id not in roots:
                roots[record.instrument_id] = record
    return roots


def _signal_records(
    publications: tuple[TradeSignalPublication, ...],
    snapshots: tuple[OpportunitySnapshot, ...],
    roots: dict[str, HermesProjectionRecord],
) -> tuple[HermesProjectionRecord, ...]:
    candidates_by_opportunity = {
        snapshot.opportunity_id: frozenset(
            candidate.symbol for candidate in snapshot.candidates
        )
        for snapshot in snapshots
    }
    records: list[HermesProjectionRecord] = []
    for publication in publications:
        signal = publication.signal
        if (
            signal.strategy_lane.market_id is not MarketId.US_EQUITIES
            or signal.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
            or signal.opportunity_id is None
            or signal.symbol not in candidates_by_opportunity.get(
                signal.opportunity_id,
                frozenset(),
            )
            or signal.symbol not in roots
        ):
            raise InvalidHermesProjectionSourceError
        records.append(
            signal_publication_projection_record(
                publication,
                frozenset(),
            ).model_copy(
                update={"root_source_event_id": roots[signal.symbol].source_event_id}
            )
        )
    return tuple(records)
