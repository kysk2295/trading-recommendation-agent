from __future__ import annotations

import datetime as dt
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

SWING_OUTBOX_NAME: Final = "trade-signals.v1.jsonl"
SWING_CARDS_NAME: Final = "trade-signal-cards-ko"
SWING_REPORT_NAME: Final = "us_swing_shadow_summary_ko.md"


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
        _file_identity(candidate)
        for candidate in database_candidates
        if candidate.exists() and candidate.is_file()
    }
    for target in (
        output / SWING_OUTBOX_NAME,
        output / SWING_REPORT_NAME,
        output / SWING_CARDS_NAME,
    ):
        if (
            target.is_symlink()
            or target.resolve(strict=False) in database_targets
            or (
                target.exists()
                and target.is_file()
                and _file_identity(target) in database_identities
            )
        ):
            raise InvalidSwingShadowCliTargetError


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


__all__ = (
    "SWING_CARDS_NAME",
    "SWING_OUTBOX_NAME",
    "SWING_REPORT_NAME",
    "InvalidSwingShadowCliTargetError",
    "SwingShadowReport",
    "harden_private_swing_cards",
    "prepare_private_swing_file",
    "validate_swing_shadow_targets",
)
