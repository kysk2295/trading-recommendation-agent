#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Final, override

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
from trading_agent.contract_outbox import (
    ContractOutboxConflictError,
    ContractOutboxFormatError,
    append_trade_signal_publication,
)
from trading_agent.private_report import write_private_report
from trading_agent.swing_new_high_rvol import (
    InvalidNewHighRvolProjectionError,
    project_new_high_rvol_signals,
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

_OUTBOX_NAME: Final = "trade-signals.v1.jsonl"
_CARDS_NAME: Final = "trade-signal-cards-ko"
_REPORT_NAME: Final = "us_swing_shadow_summary_ko.md"


class UsSwingShadowRunError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow run을 안전하게 실행할 수 없습니다"


def main(
    session_date: str | None = None,
    universe_file: str | None = None,
    fixture_root: str | None = None,
    database: str = "outputs/us_swing_shadow/swing-shadow.sqlite3",
    output_dir: str = "outputs/us_swing_shadow/latest",
    secret_path: str = str(DEFAULT_ALPACA_SECRET_PATH),
) -> None:
    parsed_session = _parse_session_date(session_date)
    database_path = Path(database).expanduser().resolve(strict=False)
    output = Path(output_dir).expanduser().resolve(strict=False)
    try:
        _validate_targets(database_path, output)
        source = (
            load_swing_daily_source(Path(fixture_root), session_date=parsed_session)
            if fixture_root is not None
            else _collect_production_source(
                parsed_session,
                universe_file=universe_file,
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
            _prepare_private_file(outbox)
        for signal in signals:
            publication = TradeSignalPublication(
                published_at=source.observed_at,
                signal=signal,
            )
            new_publications += int(
                append_trade_signal_publication(outbox, cards_dir, publication)
            )
        _harden_private_cards(cards_dir)
        write_private_report(
            output / _REPORT_NAME,
            _report(
                source_session_date=source.session_date,
                symbol_count=len(source.symbols),
                signal_count=len(signals),
                new_publications=new_publications,
                new_events=len(events),
            ),
        )
    except typer.BadParameter:
        raise
    except (
        AlpacaApiError,
        AlpacaSecretFileError,
        ContractOutboxConflictError,
        ContractOutboxFormatError,
        InvalidNewHighRvolProjectionError,
        InvalidSwingDailySourceError,
        InvalidSwingShadowEngineError,
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


def _collect_production_source(
    session_date: dt.date,
    *,
    universe_file: str | None,
    secret_path: Path,
):
    if universe_file is None:
        raise UsSwingShadowRunError
    symbols = _load_universe(Path(universe_file))
    now = _current_new_york()
    normalized = validate_current_swing_daily_collection(
        symbols=symbols,
        session_date=session_date,
        observed_at=now,
        now=now,
    )
    credentials = load_alpaca_credentials(secret_path)
    with create_alpaca_client() as data_client:
        return collect_current_swing_daily_source(
            bars_client=AlpacaBarsClient(
                data_client,
                credentials,
                request_interval_seconds=1.0,
            ),
            symbols=normalized,
            session_date=session_date,
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


def _validate_targets(database: Path, output: Path) -> None:
    database_candidates = (
        database,
        Path(f"{database}.writer.lock"),
        Path(f"{database}-journal"),
        Path(f"{database}-shm"),
        Path(f"{database}-wal"),
    )
    database_targets = {
        candidate.resolve(strict=False) for candidate in database_candidates
    }
    database_identities = {
        _file_identity(candidate)
        for candidate in database_candidates
        if candidate.exists() and candidate.is_file()
    }
    for target in (output / _OUTBOX_NAME, output / _REPORT_NAME, output / _CARDS_NAME):
        if (
            target.is_symlink()
            or target.resolve(strict=False) in database_targets
            or (
                target.exists()
                and target.is_file()
                and _file_identity(target) in database_identities
            )
        ):
            raise UsSwingShadowRunError


def _file_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def _prepare_private_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise UsSwingShadowRunError
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    path.chmod(0o600)


def _harden_private_cards(cards_dir: Path) -> None:
    if not cards_dir.exists():
        return
    if cards_dir.is_symlink() or not cards_dir.is_dir():
        raise UsSwingShadowRunError
    cards_dir.chmod(0o700)
    for card in cards_dir.iterdir():
        metadata = card.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UsSwingShadowRunError
        card.chmod(0o600)


def _report(
    *,
    source_session_date: dt.date,
    symbol_count: int,
    signal_count: int,
    new_publications: int,
    new_events: int,
) -> str:
    return "\n".join(
        (
            "# US Swing New-High RVOL Shadow 요약",
            "",
            "> 완료된 일봉만 쓰는 조건부 추천 및 shadow forward-validation입니다. "
            + "현재 호가, 자동주문, Paper 계좌 또는 확정수익 주장이 아닙니다.",
            "",
            f"- 세션: {source_session_date.isoformat()}",
            f"- 관측 종목 수: {symbol_count}",
            f"- 조건부 신호: {signal_count}",
            f"- 신규 조건부 신호: {new_publications}",
            f"- 신규 shadow event: {new_events}",
            "- 실행 모드: shadow only",
            "- broker account·order mutation: 없음",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
