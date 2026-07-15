from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, final, override

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from trading_agent.ls_nws import LsNwsRawFrame, LsNwsWireKind


class LsNwsFixtureError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS NWS fixture manifest 또는 raw frame이 유효하지 않습니다"


class LsNwsFixtureFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    sequence: StrictInt
    received_at: dt.datetime
    wire_kind: LsNwsWireKind
    payload_path: StrictStr

    @model_validator(mode="after")
    def validate_frame(self) -> Self:
        path = Path(self.payload_path)
        if (
            not 1 <= self.sequence <= 999_999
            or not _aware(self.received_at)
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("invalid LS NWS fixture frame")
        return self


class LsNwsFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    frames: tuple[LsNwsFixtureFrame, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        sequences = tuple(item.sequence for item in self.frames)
        payload_paths = tuple(item.payload_path for item in self.frames)
        received_at = tuple(item.received_at for item in self.frames)
        if (
            not self.frames
            or sequences != tuple(range(1, len(self.frames) + 1))
            or len(payload_paths) != len(set(payload_paths))
            or received_at != tuple(sorted(received_at))
        ):
            raise ValueError("invalid LS NWS fixture manifest")
        return self


@final
class _LsNwsFixtureReceiver:
    __slots__ = ("_frames", "_index")

    def __init__(self, frames: tuple[LsNwsRawFrame, ...]) -> None:
        self._frames = frames
        self._index = 0

    def receive_frame(self, timeout_seconds: float) -> LsNwsRawFrame | None:
        if timeout_seconds <= 0:
            raise LsNwsFixtureError
        if self._index >= len(self._frames):
            return None
        frame = self._frames[self._index]
        self._index += 1
        return frame


@dataclass(frozen=True, slots=True)
class LsNwsFixtureSource:
    frames: tuple[LsNwsRawFrame, ...] = field(repr=False)

    @contextmanager
    def open(self) -> Iterator[_LsNwsFixtureReceiver]:
        yield _LsNwsFixtureReceiver(self.frames)


def load_ls_nws_fixture(path: Path) -> LsNwsFixtureSource:
    try:
        if path.is_symlink():
            raise OSError
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError
        manifest = LsNwsFixtureManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        base = manifest_path.parent
        frames: list[LsNwsRawFrame] = []
        for item in manifest.frames:
            relative = Path(item.payload_path)
            candidate = base / relative
            _reject_symlink_components(base, relative)
            payload_path = candidate.resolve(strict=True)
            if not payload_path.is_relative_to(base) or not payload_path.is_file():
                raise OSError
            frames.append(
                LsNwsRawFrame(
                    sequence=item.sequence,
                    received_at=item.received_at,
                    wire_kind=item.wire_kind,
                    raw_payload=payload_path.read_bytes(),
                )
            )
        return LsNwsFixtureSource(tuple(frames))
    except (OSError, ValidationError, ValueError):
        raise LsNwsFixtureError from None


def _reject_symlink_components(base: Path, relative: Path) -> None:
    candidate = base
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise OSError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
