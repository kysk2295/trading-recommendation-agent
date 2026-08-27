from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Final, Literal, Protocol

from trading_agent.kis_auth import DEFAULT_SECRET_PATH
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    snapshots_from_sqlite_bytes,
)
from trading_agent.kr_social_signal_store import InvalidKrSocialSignalStoreError, KrSocialSignalStore
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.private_stable_file import InvalidPrivateStableFileError, read_private_stable_bytes
from trading_agent.research_agent_sources import ResearchAgentSourcePaths

V4_PLIST_FILENAME: Final = "ai.trading-agent.research-agent-runtime-v14.plist"
KST: Final = dt.timezone(dt.timedelta(hours=9))
KIS_SECRET_PATH: Final = DEFAULT_SECRET_PATH
_MAX_CALENDAR_BYTES: Final = 64 * 1024 * 1024
_MAX_KIS_SECRET_BYTES: Final = 64 * 1024


class _V4Config(Protocol):
    @property
    def schema_version(self) -> Literal[2, 3, 4]: ...

    @property
    def kr_market_receipt_root(self) -> Path | None: ...

    @property
    def kr_social_signal_database(self) -> Path | None: ...

    @property
    def source_paths(self) -> ResearchAgentSourcePaths: ...


class InvalidResearchAgentServiceV4BindingError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def require_v4_plist_filename(config: _V4Config, plist_path: Path) -> None:
    if config.schema_version == 4 and plist_path.expanduser().absolute().name != V4_PLIST_FILENAME:
        raise InvalidResearchAgentServiceV4BindingError


def verify_v4_launch_bindings(config: _V4Config, plist_path: Path) -> None:
    if config.schema_version != 4:
        return
    try:
        require_v4_plist_filename(config, plist_path)
        market_root = config.kr_market_receipt_root
        social_database = config.kr_social_signal_database
        calendar_database = config.source_paths.kr_calendar_store
        if market_root is None or social_database is None or calendar_database is None:
            raise InvalidResearchAgentServiceV4BindingError
        descriptor = open_private_parent(market_root, create=False)
        try:
            require_private_directory_query_only(descriptor)
        finally:
            os.close(descriptor)
        if not social_database.exists():
            raise InvalidResearchAgentServiceV4BindingError
        _ = KrSocialSignalStore(social_database).signals_for_task("0" * 64)
        calendar_bytes = read_private_stable_bytes(calendar_database, max_bytes=_MAX_CALENDAR_BYTES)
        snapshots = snapshots_from_sqlite_bytes(calendar_bytes)
        current_date = utc_now().astimezone(KST).date()
        if sum(snapshot.payload.base_date == current_date for snapshot in snapshots) != 1:
            raise InvalidResearchAgentServiceV4BindingError
        credentials = read_private_stable_bytes(KIS_SECRET_PATH, max_bytes=_MAX_KIS_SECRET_BYTES)
        _require_kis_live_credentials(credentials)
    except (
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrSocialSignalStoreError,
        InvalidPrivateDirectoryIdentityError,
        InvalidPrivateStableFileError,
        OSError,
        UnicodeError,
    ):
        raise InvalidResearchAgentServiceV4BindingError from None


def _require_kis_live_credentials(payload: bytes) -> None:
    values: dict[str, str] = {}
    for raw_line in payload.decode("utf-8").splitlines():
        name, separator, value = raw_line.partition("=")
        if separator:
            values[name] = value.strip()
    if not values.get("KIS_LIVE_APP_KEY") or not values.get("KIS_LIVE_APP_SECRET"):
        raise InvalidResearchAgentServiceV4BindingError
