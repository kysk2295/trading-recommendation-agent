from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FutureSessionMaterializationError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


__all__ = ("FutureSessionMaterializationError",)
