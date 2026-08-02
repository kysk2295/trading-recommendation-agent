from __future__ import annotations

from dataclasses import dataclass
from typing import override


class GeneratedStrategyExecutionError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"generated strategy execution blocked: {self.reason}"


@dataclass(frozen=True, slots=True)
class GeneratedStrategyLimits:
    wall_seconds: float = 2.0
    cpu_seconds: int = 2
    rss_bytes: int = 1024 * 1024 * 1024
    open_files: int = 32
    output_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not 0.05 <= self.wall_seconds <= 30.0
            or not 1 <= self.cpu_seconds <= 30
            or not 128 * 1024 * 1024 <= self.rss_bytes <= 10 * 1024 * 1024 * 1024
            or not 16 <= self.open_files <= 128
            or not 64 * 1024 <= self.output_bytes <= 8 * 1024 * 1024
        ):
            raise GeneratedStrategyExecutionError("resource_limits_invalid")
