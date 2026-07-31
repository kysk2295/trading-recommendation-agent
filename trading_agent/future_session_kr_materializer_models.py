from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KrFutureSessionMaterializationRequest:
    request_path: Path
    plan_path: Path
    output_dir: Path
    launch_agents_dir: Path | None = None


__all__ = ("KrFutureSessionMaterializationRequest",)
