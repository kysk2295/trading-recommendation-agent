from __future__ import annotations

import datetime as dt
import re
from typing import Protocol, final, override

from trading_agent.alpaca_sip_runtime_adapter import normalize_alpaca_sip_runtime_bars
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRuntimeBar,
)
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_intraday_volume_profile import (
    HistoricalVolumeSession,
    IntradayVolumeProfileEvidence,
    build_intraday_volume_profile,
)
from trading_agent.us_intraday_volume_profile_models import (
    intraday_volume_profile_source_dates,
)

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,15}$")


class AlpacaSipHistoricalProfileError(ValueError):
    @override
    def __str__(self) -> str:
        return "alpaca SIP historical profile is blocked"


class HistoricalProfileProjector(Protocol):
    def project(
        self,
        page_set: AlpacaSipMinutePage,
        instrument_id: str,
        bars: tuple[AlpacaSipRuntimeBar, ...],
    ) -> ResearchInputIdentity: ...


@final
class AlpacaSipHistoricalProfileCollector:
    __slots__ = ("_page_client", "_projector", "_store")

    def __init__(
        self,
        page_client: AlpacaSipMinutePageClient,
        store: AlpacaSipRuntimeEvidenceStore,
        projector: HistoricalProfileProjector,
    ) -> None:
        if (
            type(page_client) is not AlpacaSipMinutePageClient
            or type(store) is not AlpacaSipRuntimeEvidenceStore
            or not callable(getattr(projector, "project", None))
        ):
            raise AlpacaSipHistoricalProfileError
        self._page_client = page_client
        self._store = store
        self._projector = projector

    def collect(
        self,
        instrument_id: str,
        symbol: str,
        target_session_date: dt.date,
        *,
        through_minute: int,
    ) -> IntradayVolumeProfileEvidence:
        try:
            if (
                type(instrument_id) is not str
                or not instrument_id
                or type(symbol) is not str
                or _SYMBOL.fullmatch(symbol) is None
            ):
                raise AlpacaSipHistoricalProfileError
            dates = intraday_volume_profile_source_dates(target_session_date, through_minute)
            sessions = tuple(self._session(instrument_id, symbol, session_date) for session_date in dates)
            return build_intraday_volume_profile(
                instrument_id,
                target_session_date,
                through_minute=through_minute,
                sessions=sessions,
            )
        except (OSError, TypeError, ValueError):
            raise AlpacaSipHistoricalProfileError from None

    def _session(
        self,
        instrument_id: str,
        symbol: str,
        session_date: dt.date,
    ) -> HistoricalVolumeSession:
        bounds = regular_session_bounds(session_date)
        if bounds is None:
            raise AlpacaSipHistoricalProfileError
        opened, closed = bounds
        request = AlpacaSipMinutePageRequest(
            session_date,
            symbol,
            opened,
            closed - dt.timedelta(microseconds=1),
        )
        page_set = self._store.load_page_set(request)
        if page_set is None:
            page_set = self._page_client.fetch_page(request)
        for page in page_set.pages:
            _ = self._store.append_page(request, page)
        bars = normalize_alpaca_sip_runtime_bars(page_set, opened, closed)
        expected_count = int((closed - opened) / dt.timedelta(minutes=1))
        if tuple(bar.sequence for bar in bars) != tuple(range(1, expected_count + 1)):
            raise AlpacaSipHistoricalProfileError
        identity = self._projector.project(page_set, instrument_id, bars)
        return HistoricalVolumeSession(
            session_date,
            identity,
            tuple(bar.completed_bar for bar in bars),
        )


__all__ = (
    "AlpacaSipHistoricalProfileCollector",
    "AlpacaSipHistoricalProfileError",
)
