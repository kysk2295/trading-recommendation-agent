from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.daily_research_contract import strategy_contract
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.intraday_research_loop_models import IntradayResearchManifest, IntradayReviewerDecision
from trading_agent.intraday_research_reviewer import IntradayReviewRequest, review_intraday_experiment
from trading_agent.intraday_research_trial import IntradayTrialExecutionContext, run_or_replay_intraday_trial
from trading_agent.lane_registry_store import LaneRegistryReader
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.replay import load_bounded_bars


class IntradayResearchLoopError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "bounded intraday research and review loop failed"


@dataclass(frozen=True, slots=True)
class IntradayResearchLoopPaths:
    input_csv: Path
    lane_registry: Path
    experiment_ledger: Path
    artifact_root: Path
    review_root: Path


@dataclass(frozen=True, slots=True)
class IntradayResearchLoopResult:
    trials_total: int
    experiment_artifacts_created: int
    review_artifacts_created: int
    decisions: tuple[IntradayReviewerDecision, ...]


def run_intraday_research_loop(
    manifest: IntradayResearchManifest,
    paths: IntradayResearchLoopPaths,
) -> IntradayResearchLoopResult:
    if any(
        strategy_contract(item.strategy).hypothesis_id != item.hypothesis_id
        for item in manifest.hypotheses
    ):
        raise IntradayResearchLoopError
    bars = load_bounded_bars(
        paths.input_csv,
        max_rows=manifest.max_bars,
        max_sessions=manifest.max_sessions,
    )
    data_version = _file_sha256(paths.input_csv)
    manifest_sha256 = hashlib.sha256(canonical_experiment_ledger_json(manifest).encode()).hexdigest()
    _ = bootstrap_current_intraday_experiments(
        lane_registry=LaneRegistryReader(paths.lane_registry),
        experiment_ledger=ExperimentLedgerStore(paths.experiment_ledger),
        code_version=manifest.code_version,
        recorded_at=manifest.registered_at,
    )
    context = IntradayTrialExecutionContext(
        manifest=manifest,
        experiment_ledger=paths.experiment_ledger,
        artifact_root=paths.artifact_root,
        data_version=data_version,
        manifest_sha256=manifest_sha256,
        bars=bars,
    )
    experiment_created = 0
    review_created = 0
    decisions: list[IntradayReviewerDecision] = []
    with _heavy_empirical_lease(paths.experiment_ledger):
        for strategy in manifest.strategies:
            experiment, created = run_or_replay_intraday_trial(context, strategy)
            experiment_created += int(created)
            review, created = review_intraday_experiment(
                IntradayReviewRequest(
                    ledger=ExperimentLedgerReader(paths.experiment_ledger),
                    experiment=experiment,
                    review_root=paths.review_root,
                    reviewed_at=manifest.registered_at + dt.timedelta(seconds=4),
                )
            )
            review_created += int(created)
            decisions.append(review.payload.decision)
    return IntradayResearchLoopResult(
        trials_total=len(manifest.strategies),
        experiment_artifacts_created=experiment_created,
        review_artifacts_created=review_created,
        decisions=tuple(decisions),
    )


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@contextmanager
def _heavy_empirical_lease(ledger_path: Path) -> Iterator[None]:
    lock_path = absolute_private_path(Path(f"{ledger_path}.m6-heavy.lock"))
    parent = descriptor = -1
    parent_locked = descriptor_locked = False
    try:
        parent = open_private_parent(lock_path.parent, create=True)
        require_private_directory(parent)
        descriptor = os.open(
            lock_path.name,
            os.O_CLOEXEC | os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        _require_lease_binding(lock_path, parent, descriptor)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor_locked = True
        _require_lease_binding(lock_path, parent, descriptor)
    except (BlockingIOError, InvalidPrivateDirectoryIdentityError, OSError, ValueError):
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)
        raise IntradayResearchLoopError from None
    try:
        yield
        _require_lease_binding(lock_path, parent, descriptor)
    except (InvalidPrivateDirectoryIdentityError, OSError, ValueError):
        raise IntradayResearchLoopError from None
    finally:
        if descriptor_locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if parent_locked:
            fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(descriptor)
        os.close(parent)


def _require_lease_binding(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
    ):
        raise IntradayResearchLoopError


__all__ = (
    "IntradayResearchLoopError",
    "IntradayResearchLoopPaths",
    "IntradayResearchLoopResult",
    "run_intraday_research_loop",
)
