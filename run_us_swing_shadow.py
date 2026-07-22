#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import override

import typer
from rich import print as rprint

from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaApiError,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    create_alpaca_client,
    load_alpaca_credentials,
)
from trading_agent.alpaca_most_active import (
    AlpacaMostActiveClient,
    InvalidAlpacaMostActiveSourceError,
)
from trading_agent.contract_outbox import (
    ContractOutboxConflictError,
    ContractOutboxFormatError,
    append_trade_signal_publication,
)
from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_projection import InvalidHermesProjectionSourceError
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_report import write_private_report
from trading_agent.swing_new_high_rvol import (
    InvalidNewHighRvolProjectionError,
    project_new_high_rvol_signals,
)
from trading_agent.swing_shadow_cli_files import (
    SWING_CARDS_NAME as _CARDS_NAME,
)
from trading_agent.swing_shadow_cli_files import (
    SWING_OUTBOX_NAME as _OUTBOX_NAME,
)
from trading_agent.swing_shadow_cli_files import (
    SWING_REPORT_NAME as _REPORT_NAME,
)
from trading_agent.swing_shadow_cli_files import (
    InvalidSwingShadowCliTargetError,
    SwingShadowReport,
    harden_private_swing_cards,
    prepare_private_swing_file,
    validate_swing_shadow_targets,
)
from trading_agent.swing_shadow_delivery import (
    InvalidSwingShadowDeliveryError,
    project_swing_shadow_cycle_delivery,
)
from trading_agent.swing_shadow_engine import (
    InvalidSwingShadowEngineError,
    advance_swing_shadow_session,
)
from trading_agent.swing_shadow_source import (
    InvalidSwingDailySourceError,
    collect_current_swing_daily_source,
    load_swing_daily_source,
    validate_current_swing_daily_collection,
)
from trading_agent.swing_shadow_store import (
    InvalidSwingShadowLedgerError,
    SwingShadowConflictError,
    SwingShadowStore,
    SwingShadowWriterLeaseUnavailableError,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_equity_calendar import NEW_YORK


class UsSwingShadowRunError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow run을 안전하게 실행할 수 없습니다"


@dataclass(frozen=True, slots=True)
class _ProductionSourceRequest:
    session_date: dt.date
    universe_file: str | None
    auto_universe: bool
    secret_path: Path


def main(
    session_date: str | None = None,
    universe_file: str | None = None,
    auto_universe: bool = False,
    fixture_root: str | None = None,
    database: str = "outputs/us_swing_shadow/swing-shadow.sqlite3",
    delivery_database: str | None = None,
    output_dir: str = "outputs/us_swing_shadow/latest",
    secret_path: str = str(DEFAULT_ALPACA_SECRET_PATH),
) -> None:
    parsed_session = _parse_session_date(session_date)
    database_path = Path(database).expanduser().resolve(strict=False)
    output = Path(output_dir).expanduser().resolve(strict=False)
    delivery_path = _delivery_path(delivery_database, fixture_root, output)
    try:
        validate_swing_shadow_targets(database_path, delivery_path, output)
        if fixture_root is not None:
            if universe_file is not None or auto_universe:
                raise UsSwingShadowRunError
            source = load_swing_daily_source(Path(fixture_root), session_date=parsed_session)
        else:
            source = _collect_production_source(
                _ProductionSourceRequest(
                    session_date=parsed_session,
                    universe_file=universe_file,
                    auto_universe=auto_universe,
                    secret_path=Path(secret_path),
                )
            )
        signals = project_new_high_rvol_signals(source)
        store = SwingShadowStore(database_path)
        with store.writer() as writer:
            events = advance_swing_shadow_session(writer, source=source, signals=signals)

        outbox = output / _OUTBOX_NAME
        cards_dir = output / _CARDS_NAME
        new_publications = 0
        if signals or outbox.exists():
            prepare_private_swing_file(outbox)
        for signal in signals:
            publication = TradeSignalPublication(
                published_at=source.observed_at,
                signal=signal,
            )
            new_publications += int(
                append_trade_signal_publication(outbox, cards_dir, publication)
            )
        harden_private_swing_cards(cards_dir)
        with HermesDeliveryStore(delivery_path).writer() as writer:
            delivery = project_swing_shadow_cycle_delivery(source, signals, writer)
        write_private_report(
            output / _REPORT_NAME,
            SwingShadowReport(
                session_date=source.session_date,
                symbol_count=len(source.symbols),
                signal_count=len(signals),
                new_publications=new_publications,
                new_events=len(events),
                new_deliveries=delivery.inserted,
            ).render(),
        )
    except typer.BadParameter:
        raise
    except (
        AlpacaApiError,
        AlpacaSecretFileError,
        ContractOutboxConflictError,
        ContractOutboxFormatError,
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesProjectionSourceError,
        InvalidNewHighRvolProjectionError,
        InvalidAlpacaMostActiveSourceError,
        InvalidSwingShadowCliTargetError,
        InvalidSwingDailySourceError,
        InvalidSwingShadowEngineError,
        InvalidSwingShadowDeliveryError,
        InvalidSwingShadowLedgerError,
        MissingAlpacaCredentialsError,
        OSError,
        SwingShadowConflictError,
        SwingShadowWriterLeaseUnavailableError,
        UsSwingShadowRunError,
        ValueError,
    ):
        raise typer.BadParameter(str(UsSwingShadowRunError())) from None

    rprint(
        "[green]완료[/green] US swing shadow "
        + f"조건부 신호 {len(signals)}건, 신규 발행 {new_publications}건, "
        + f"신규 shadow event {len(events)}건"
    )


def _collect_production_source(request: _ProductionSourceRequest):
    now = _current_new_york()
    if request.auto_universe:
        if request.universe_file is not None:
            raise UsSwingShadowRunError
        symbols = ("SPY",)
    else:
        if request.universe_file is None:
            raise UsSwingShadowRunError
        symbols = _load_universe(Path(request.universe_file))
    _ = validate_current_swing_daily_collection(
        symbols=symbols, session_date=request.session_date, observed_at=now, now=now
    )
    credentials = load_alpaca_credentials(request.secret_path)
    with create_alpaca_client() as data_client:
        if request.auto_universe:
            symbols = AlpacaMostActiveClient(data_client, credentials).fetch(
                top=50,
                session_date=request.session_date,
                observed_at=now,
            ).scanner_symbols
        normalized = validate_current_swing_daily_collection(
            symbols=symbols,
            session_date=request.session_date,
            observed_at=now,
            now=now,
        )
        return collect_current_swing_daily_source(
            bars_client=AlpacaBarsClient(
                data_client,
                credentials,
                request_interval_seconds=1.0,
            ),
            symbols=normalized,
            session_date=request.session_date,
            observed_at=now,
            universe_id=_universe_id(normalized),
            now=now,
        )


def _parse_session_date(value: str | None) -> dt.date:
    if value is None:
        raise typer.BadParameter("session date가 필요합니다")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter("session date는 YYYY-MM-DD여야 합니다") from None
    if parsed.isoformat() != value:
        raise typer.BadParameter("session date는 YYYY-MM-DD여야 합니다")
    return parsed


def _load_universe(path: Path) -> tuple[str, ...]:
    symbols = tuple(
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not symbols:
        raise UsSwingShadowRunError
    return symbols


def _universe_id(symbols: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\n".join(symbols).encode("ascii")).hexdigest()[:16]
    return f"us_swing_universe_{digest}"


def _current_new_york() -> dt.datetime:
    return dt.datetime.now(NEW_YORK)


def _delivery_path(value: str | None, fixture_root: str | None, output: Path) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve(strict=False)
    if fixture_root is not None:
        return output / "hermes-delivery.sqlite3"
    return Path("outputs/hermes/delivery.sqlite3").resolve(strict=False)


if __name__ == "__main__":
    typer.run(main)
