from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import override

from trading_agent.strategy_research_methodologies import ResamplingMethod


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass(frozen=True, slots=True)
class BootstrapPolicy:
    repetitions: int
    seed: int
    familywise_alpha: float
    adjustment_tests: int


@dataclass(frozen=True, slots=True)
class ResamplingMetadataError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def fixed_seed_percentile_mean(
    values: tuple[float, ...],
    policy: BootstrapPolicy,
) -> BootstrapInterval:
    sample_size = len(values)
    generator = random.Random(policy.seed)
    estimates = sorted(
        sum(values[generator.randrange(sample_size)] for _ in range(sample_size)) / sample_size
        for _ in range(policy.repetitions)
    )
    adjusted_alpha = policy.familywise_alpha / policy.adjustment_tests
    return BootstrapInterval(
        estimate=sum(values) / sample_size,
        lower=_quantile(estimates, adjusted_alpha / 2),
        upper=_quantile(estimates, 1 - adjusted_alpha / 2),
    )


def fixed_seed_resampled_mean(
    values: tuple[float, ...],
    policy: BootstrapPolicy,
    method: ResamplingMethod,
    cluster_keys: tuple[str, ...] = (),
) -> BootstrapInterval:
    _require_cluster_keys(values, cluster_keys)
    match method:
        case ResamplingMethod.SESSION_MOVING_BLOCK:
            estimates = _moving_block_estimates(values, cluster_keys, policy)
        case (
            ResamplingMethod.EVENT_CLUSTER
            | ResamplingMethod.DATE_CLUSTER
            | ResamplingMethod.UNDERLYING_MATURITY_CLUSTER
        ):
            estimates = _cluster_estimates(values, cluster_keys, policy)
    adjusted_alpha = policy.familywise_alpha / policy.adjustment_tests
    return BootstrapInterval(
        estimate=sum(values) / len(values),
        lower=_quantile(estimates, adjusted_alpha / 2),
        upper=_quantile(estimates, 1 - adjusted_alpha / 2),
    )


def _moving_block_estimates(
    values: tuple[float, ...],
    session_keys: tuple[str, ...],
    policy: BootstrapPolicy,
) -> list[float]:
    sessions = tuple(dict.fromkeys(session_keys))
    grouped = tuple(
        tuple(value for value, key in zip(values, session_keys, strict=True) if key == session) for session in sessions
    )
    generator = random.Random(policy.seed)
    estimates: list[float] = []
    for _ in range(policy.repetitions):
        sampled: tuple[float, ...] = ()
        for session in grouped:
            block_size = min(len(session), max(2, math.isqrt(len(session))))
            block_count = math.ceil(len(session) / block_size)
            sampled += tuple(
                session[(generator.randrange(len(session)) + offset) % len(session)]
                for _ in range(block_count)
                for offset in range(block_size)
            )[: len(session)]
        estimates.append(sum(sampled) / len(sampled))
    return sorted(estimates)


def _require_cluster_keys(values: tuple[float, ...], cluster_keys: tuple[str, ...]) -> None:
    if len(cluster_keys) != len(values) or any(not key for key in cluster_keys):
        raise ResamplingMetadataError("resampling_cluster_keys_invalid")


def _cluster_estimates(
    values: tuple[float, ...],
    cluster_keys: tuple[str, ...],
    policy: BootstrapPolicy,
) -> list[float]:
    keys = tuple(dict.fromkeys(cluster_keys))
    clusters = {
        key: tuple(value for value, item_key in zip(values, cluster_keys, strict=True) if item_key == key)
        for key in keys
    }
    generator = random.Random(policy.seed)
    estimates: list[float] = []
    for _ in range(policy.repetitions):
        sampled = tuple(value for _ in keys for value in clusters[keys[generator.randrange(len(keys))]])
        estimates.append(sum(sampled) / len(sampled))
    return sorted(estimates)


def _quantile(values: list[float], probability: float) -> float:
    position = probability * (len(values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    return values[lower_index] + (values[upper_index] - values[lower_index]) * fraction


__all__ = (
    "BootstrapInterval",
    "BootstrapPolicy",
    "ResamplingMetadataError",
    "fixed_seed_percentile_mean",
    "fixed_seed_resampled_mean",
)
