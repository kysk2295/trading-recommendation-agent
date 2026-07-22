from __future__ import annotations

import datetime as dt
import fcntl
import os
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, assert_never, final

from trading_agent.hermes_delivery_errors import HermesDeliveryWriterLeaseUnavailableError
from trading_agent.hermes_delivery_models import (
    HermesDeliveryFailure,
    HermesDeliveryKind,
    HermesDeliveryTransitionKind,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore

_CURRENT_MARKET_EVENT_MAX_AGE: Final = dt.timedelta(seconds=30)


class RetryableHermesPlatformError(RuntimeError):
    def __str__(self) -> str:
        return "Hermes platform delivery can be retried"


class TerminalHermesPlatformError(RuntimeError):
    def __str__(self) -> str:
        return "Hermes platform delivery was rejected"


class HermesDeliveryServiceLeaseUnavailableError(RuntimeError):
    def __str__(self) -> str:
        return "Hermes delivery service lease is unavailable"


class InvalidHermesDeliveryServiceError(ValueError):
    def __str__(self) -> str:
        return "Hermes delivery service is invalid"


class HermesDeliveryTickStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    ACKNOWLEDGED = "acknowledged"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    SUPPRESSED = "suppressed"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class HermesDeliverySendRequest:
    delivery_id: str
    text: str
    reply_to_message_id: str | None


@dataclass(frozen=True, slots=True)
class HermesPlatformAcknowledgement:
    message_id: str


@dataclass(frozen=True, slots=True)
class HermesDeliveryTickResult:
    status: HermesDeliveryTickStatus
    delivery_id: str | None = None
    platform_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class HermesDeliveryWorkerSettings:
    worker_id: str = "hermes-plugin"
    lease_seconds: int = 30
    retry_delay_seconds: int = 5
    clock: Callable[[], dt.datetime] = _utc_now


class HermesDeliverySender(Protocol):
    def send(self, request: HermesDeliverySendRequest) -> HermesPlatformAcknowledgement: ...


@final
class HermesDeliveryWorker:
    __slots__ = ("_sender", "_settings", "_store")

    def __init__(
        self,
        store: HermesDeliveryStore,
        sender: HermesDeliverySender,
        settings: HermesDeliveryWorkerSettings | None = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._settings = HermesDeliveryWorkerSettings() if settings is None else settings

    def tick(self) -> HermesDeliveryTickResult:
        now = self._settings.clock()
        try:
            with self._store.writer() as writer:
                claim = writer.claim_next(
                    worker_id=self._settings.worker_id,
                    now=now,
                    lease_seconds=self._settings.lease_seconds,
                )
                if claim is None:
                    return HermesDeliveryTickResult(status=HermesDeliveryTickStatus.IDLE)
                match claim.event.kind:
                    case HermesDeliveryKind.WATCH | HermesDeliveryKind.ACTIONABLE:
                        age = now - claim.event.occurred_at
                        if age < dt.timedelta(0) or age > _CURRENT_MARKET_EVENT_MAX_AGE:
                            _ = writer.fail(
                                claim,
                                HermesDeliveryFailure(
                                    failed_at=now,
                                    reason="market_event_ineligible",
                                    retry_delay_seconds=0,
                                    terminal=True,
                                ),
                            )
                            return HermesDeliveryTickResult(
                                status=HermesDeliveryTickStatus.SUPPRESSED,
                                delivery_id=claim.event.delivery_id,
                            )
                    case (
                        HermesDeliveryKind.INVALIDATION
                        | HermesDeliveryKind.EXIT
                        | HermesDeliveryKind.INCIDENT
                        | HermesDeliveryKind.NO_RECOMMENDATION
                        | HermesDeliveryKind.RESEARCH
                        | HermesDeliveryKind.DAILY_SUMMARY
                    ):
                        pass
                    case unreachable:
                        assert_never(unreachable)
                request = HermesDeliverySendRequest(
                    delivery_id=claim.event.delivery_id,
                    text=claim.event.rendered_text,
                    reply_to_message_id=claim.lineage.root_platform_message_id,
                )
                try:
                    acknowledgement = self._sender.send(request)
                except RetryableHermesPlatformError:
                    failure = HermesDeliveryFailure(
                        failed_at=self._settings.clock(),
                        reason="telegram_timeout",
                        retry_delay_seconds=self._settings.retry_delay_seconds,
                    )
                    transition = writer.fail(claim, failure)
                    match transition.kind:
                        case HermesDeliveryTransitionKind.RETRY_SCHEDULED:
                            status = HermesDeliveryTickStatus.RETRY_SCHEDULED
                        case HermesDeliveryTransitionKind.DEAD_LETTER:
                            status = HermesDeliveryTickStatus.DEAD_LETTERED
                        case unreachable:
                            assert_never(unreachable)
                    return HermesDeliveryTickResult(status=status, delivery_id=claim.event.delivery_id)
                except TerminalHermesPlatformError:
                    failure = HermesDeliveryFailure(
                        failed_at=self._settings.clock(),
                        reason="telegram_rejected",
                        retry_delay_seconds=0,
                        terminal=True,
                    )
                    _ = writer.fail(claim, failure)
                    return HermesDeliveryTickResult(
                        status=HermesDeliveryTickStatus.DEAD_LETTERED,
                        delivery_id=claim.event.delivery_id,
                    )
                _ = writer.acknowledge(
                    claim,
                    platform_message_id=acknowledgement.message_id,
                    acknowledged_at=self._settings.clock(),
                )
                return HermesDeliveryTickResult(
                    status=HermesDeliveryTickStatus.ACKNOWLEDGED,
                    delivery_id=claim.event.delivery_id,
                    platform_message_id=acknowledgement.message_id,
                )
        except HermesDeliveryWriterLeaseUnavailableError:
            return HermesDeliveryTickResult(status=HermesDeliveryTickStatus.BUSY)


class _DeliveryDaemonState:
    """Process-local mutable ownership state for the single daemon thread."""

    __slots__ = ("lock", "thread")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None


_DAEMON_STATE: Final = _DeliveryDaemonState()


def start_delivery_daemon(
    database: Path,
    sender: HermesDeliverySender,
    *,
    poll_seconds: float = 1.0,
) -> bool:
    with _DAEMON_STATE.lock:
        if _DAEMON_STATE.thread is not None and _DAEMON_STATE.thread.is_alive():
            return False
        thread = threading.Thread(
            target=_run_daemon,
            args=(database, sender, poll_seconds),
            name="hermes-trading-delivery",
            daemon=True,
        )
        thread.start()
        _DAEMON_STATE.thread = thread
        return True


def delivery_daemon_status() -> str:
    with _DAEMON_STATE.lock:
        if _DAEMON_STATE.thread is None:
            return "stopped"
        return "running" if _DAEMON_STATE.thread.is_alive() else "failed"


def run_delivery_service(
    database: Path,
    sender: HermesDeliverySender,
    *,
    poll_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
) -> None:
    if not database.is_absolute() or poll_seconds <= 0 or poll_seconds > 60:
        raise InvalidHermesDeliveryServiceError
    pause = threading.Event() if stop_event is None else stop_event
    with _service_lease(database):
        worker = HermesDeliveryWorker(store=HermesDeliveryStore(database), sender=sender)
        while not pause.is_set():
            _ = worker.tick()
            _ = pause.wait(poll_seconds)


def _run_daemon(database: Path, sender: HermesDeliverySender, poll_seconds: float) -> None:
    try:
        run_delivery_service(database, sender, poll_seconds=poll_seconds)
    except (HermesDeliveryServiceLeaseUnavailableError, InvalidHermesDeliveryServiceError, OSError):
        return


@contextmanager
def _service_lease(database: Path) -> Iterator[None]:
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        f"{database}.service.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise InvalidHermesDeliveryServiceError
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise HermesDeliveryServiceLeaseUnavailableError from error
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
