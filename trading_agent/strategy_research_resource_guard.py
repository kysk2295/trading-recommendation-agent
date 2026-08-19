from __future__ import annotations

import resource
import sys
from typing import Final, override

SCIENCE_KERNEL_RSS_LIMIT_BYTES: Final = 10 * 1024**3


class ScienceKernelRssLimitError(RuntimeError):
    __slots__ = ("limit_bytes", "observed_bytes")

    def __init__(self, observed_bytes: int, limit_bytes: int) -> None:
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes
        super().__init__(observed_bytes, limit_bytes)

    @override
    def __str__(self) -> str:
        return "science kernel RSS limit reached"


def current_process_peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def require_science_kernel_rss_below_limit() -> None:
    observed = current_process_peak_rss_bytes()
    if observed >= SCIENCE_KERNEL_RSS_LIMIT_BYTES:
        raise ScienceKernelRssLimitError(observed, SCIENCE_KERNEL_RSS_LIMIT_BYTES)


__all__ = (
    "SCIENCE_KERNEL_RSS_LIMIT_BYTES",
    "ScienceKernelRssLimitError",
    "current_process_peak_rss_bytes",
    "require_science_kernel_rss_below_limit",
)
