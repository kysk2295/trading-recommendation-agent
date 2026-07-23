from __future__ import annotations

import datetime as dt
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.swing_shadow_models import SwingDailySource

SWING_OUTBOX_NAME: Final = "trade-signals.v1.jsonl"
SWING_CARDS_NAME: Final = "trade-signal-cards-ko"
SWING_REPORT_NAME: Final = "us_swing_shadow_summary_ko.md"
SWING_SOURCES_DIR: Final = "daily-sources"
_MAX_SOURCE_BYTES: Final = 8 * 1024 * 1024


class InvalidSwingShadowCliTargetError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow output target is invalid"


@dataclass(frozen=True, slots=True)
class SwingShadowReport:
    session_date: dt.date
    symbol_count: int
    signal_count: int
    new_publications: int
    new_events: int
    new_deliveries: int

    def render(self) -> str:
        return "\n".join(
            (
                "# US Swing New-High RVOL Shadow 요약",
                "",
                "> 완료된 일봉만 쓰는 조건부 추천 및 shadow forward-validation입니다. "
                + "현재 호가, 자동주문, Paper 계좌 또는 확정수익 주장이 아닙니다.",
                "",
                f"- 세션: {self.session_date.isoformat()}",
                f"- 관측 종목 수: {self.symbol_count}",
                f"- 조건부 신호: {self.signal_count}",
                f"- 신규 조건부 신호: {self.new_publications}",
                f"- 신규 shadow event: {self.new_events}",
                f"- 신규 Hermes 전달: {self.new_deliveries}",
                "- 실행 모드: shadow only",
                "- broker account·order mutation: 없음",
                "",
            )
        )


def validate_swing_shadow_targets(
    database: Path,
    delivery_database: Path,
    output: Path,
) -> None:
    database_candidates = (*_database_candidates(database), *_database_candidates(delivery_database))
    database_targets = {candidate.resolve(strict=False) for candidate in database_candidates}
    if len(database_targets) != len(database_candidates):
        raise InvalidSwingShadowCliTargetError
    database_identities = {
        _file_identity(candidate) for candidate in database_candidates if candidate.exists() and candidate.is_file()
    }
    for target in (
        output / SWING_OUTBOX_NAME,
        output / SWING_REPORT_NAME,
        output / SWING_CARDS_NAME,
        output / SWING_SOURCES_DIR,
    ):
        if (
            target.is_symlink()
            or target.resolve(strict=False) in database_targets
            or (target.exists() and target.is_file() and _file_identity(target) in database_identities)
        ):
            raise InvalidSwingShadowCliTargetError


def write_private_swing_source(output: Path, source: SwingDailySource) -> Path:
    source_dir = output / SWING_SOURCES_DIR
    if source_dir.is_symlink() or (source_dir.exists() and not source_dir.is_dir()):
        raise InvalidSwingShadowCliTargetError
    source_dir.mkdir(parents=True, exist_ok=True)
    source_dir.chmod(0o700)
    payload = (
        json.dumps(
            source.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    destination = source_dir / f"swing_daily_source_{source.source_key}.json"
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        if _read_private_file(destination) != payload:
            raise InvalidSwingShadowCliTargetError from None
        return destination
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def load_private_swing_sources(output: Path) -> tuple[SwingDailySource, ...]:
    source_dir = output / SWING_SOURCES_DIR
    if not source_dir.exists():
        return ()
    try:
        metadata = source_dir.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.getuid()
        ):
            raise InvalidSwingShadowCliTargetError
        paths = tuple(sorted(source_dir.iterdir()))
        sources = tuple(SwingDailySource.model_validate_json(_read_private_file(path)) for path in paths)
        if len(sources) != len({source.source_key for source in sources}) or any(
            path.name != f"swing_daily_source_{source.source_key}.json"
            for path, source in zip(paths, sources, strict=True)
        ):
            raise InvalidSwingShadowCliTargetError
        return tuple(
            sorted(
                sources,
                key=lambda source: (
                    source.session_date,
                    source.source_id.canonical_id,
                    source.source_key,
                ),
            )
        )
    except InvalidSwingShadowCliTargetError:
        raise
    except (OSError, TypeError, ValueError):
        raise InvalidSwingShadowCliTargetError from None


def prepare_private_swing_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise InvalidSwingShadowCliTargetError
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not path.is_file():
            raise InvalidSwingShadowCliTargetError from None
    else:
        os.close(descriptor)
    path.chmod(0o600)


def harden_private_swing_cards(cards_dir: Path) -> None:
    if not cards_dir.exists():
        return
    if cards_dir.is_symlink() or not cards_dir.is_dir():
        raise InvalidSwingShadowCliTargetError
    cards_dir.chmod(0o700)
    for card in cards_dir.iterdir():
        metadata = card.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise InvalidSwingShadowCliTargetError
        card.chmod(0o600)


def _database_candidates(database: Path) -> tuple[Path, ...]:
    return (
        database,
        Path(f"{database}.writer.lock"),
        Path(f"{database}-journal"),
        Path(f"{database}-shm"),
        Path(f"{database}-wal"),
    )


def _file_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def _read_private_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or not 0 < metadata.st_size <= _MAX_SOURCE_BYTES
        ):
            raise InvalidSwingShadowCliTargetError
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


__all__ = (
    "SWING_CARDS_NAME",
    "SWING_OUTBOX_NAME",
    "SWING_REPORT_NAME",
    "SWING_SOURCES_DIR",
    "InvalidSwingShadowCliTargetError",
    "SwingShadowReport",
    "harden_private_swing_cards",
    "load_private_swing_sources",
    "prepare_private_swing_file",
    "validate_swing_shadow_targets",
    "write_private_swing_source",
)
