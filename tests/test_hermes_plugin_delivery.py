from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

from trading_agent.hermes_delivery_models import HermesDeliveryKind, build_hermes_delivery_event
from trading_agent.hermes_delivery_store import HermesDeliveryStore

AT = dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC)


class ManualClock:
    __slots__ = ("current",)

    def __init__(self, current: dt.datetime = AT) -> None:
        self.current = current

    def __call__(self) -> dt.datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += dt.timedelta(seconds=seconds)


class FakeTelegramSender:
    __slots__ = ("calls", "during_send", "message_ids", "retry_failures", "terminal_failure")

    def __init__(
        self,
        message_ids: list[str],
        retry_failures: int = 0,
        terminal_failure: bool = False,
    ) -> None:
        self.message_ids = message_ids
        self.retry_failures = retry_failures
        self.terminal_failure = terminal_failure
        self.calls: list = []
        self.during_send: Callable[[], None] | None = None

    def send(self, request):
        self.calls.append(request)
        if self.during_send is not None:
            callback = self.during_send
            self.during_send = None
            callback()
        if self.terminal_failure:
            raise _worker_module().TerminalHermesPlatformError
        if self.retry_failures:
            self.retry_failures -= 1
            raise _worker_module().RetryableHermesPlatformError
        return _worker_module().HermesPlatformAcknowledgement(message_id=self.message_ids.pop(0))


def test_worker_acknowledges_telegram_message_and_replies_to_root(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    root = _event("signal-1")
    reply = _event("signal-1-exit", root_delivery_id=root.delivery_id, kind=HermesDeliveryKind.EXIT)
    with store.writer() as writer:
        _ = writer.append_event(root)
        _ = writer.append_event(reply)
    sender = FakeTelegramSender(message_ids=["100", "101"])
    worker = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=ManualClock()),
    )

    # When
    first = worker.tick()
    second = worker.tick()

    # Then
    assert first.status is worker_module.HermesDeliveryTickStatus.ACKNOWLEDGED
    assert second.status is worker_module.HermesDeliveryTickStatus.ACKNOWLEDGED
    assert sender.calls[1].reply_to_message_id == "100"
    assert store.acknowledgements()[-1].platform_message_id == "101"


def test_timeout_retry_keeps_same_delivery_identity(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1"))
    sender = FakeTelegramSender(message_ids=["100"], retry_failures=1)
    clock = ManualClock()
    worker = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=clock),
    )

    # When
    retry = worker.tick()
    clock.advance(seconds=5)
    acknowledged = worker.tick()

    # Then
    assert retry.status is worker_module.HermesDeliveryTickStatus.RETRY_SCHEDULED
    assert acknowledged.status is worker_module.HermesDeliveryTickStatus.ACKNOWLEDGED
    assert sender.calls[0].delivery_id == sender.calls[1].delivery_id
    assert len(store.events()) == 1


def test_restart_after_platform_ack_uses_durable_message_identity(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1"))
    sender = FakeTelegramSender(message_ids=["100"])
    first_process = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=ManualClock()),
    )
    assert first_process.tick().status is worker_module.HermesDeliveryTickStatus.ACKNOWLEDGED

    # When
    restarted = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=ManualClock()),
    )
    result = restarted.tick()

    # Then
    assert result.status is worker_module.HermesDeliveryTickStatus.IDLE
    assert len(sender.calls) == 1
    assert store.acknowledgements()[0].platform_message_id == "100"


def test_terminal_platform_rejection_dead_letters_immediately(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1", max_attempts=3))
    sender = FakeTelegramSender(message_ids=[], terminal_failure=True)
    worker = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=ManualClock()),
    )

    # When
    result = worker.tick()

    # Then
    assert result.status is worker_module.HermesDeliveryTickStatus.DEAD_LETTERED
    assert len(store.attempts()) == 1
    assert len(store.dead_letters()) == 1


def test_retry_budget_exhaustion_dead_letters_instead_of_raising(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1", max_attempts=1))
    sender = FakeTelegramSender(message_ids=[], retry_failures=1)
    worker = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(clock=ManualClock()),
    )

    # When
    result = worker.tick()

    # Then
    assert result.status is worker_module.HermesDeliveryTickStatus.DEAD_LETTERED
    assert len(store.dead_letters()) == 1


def test_two_workers_cannot_own_the_writer_lease_simultaneously(tmp_path: Path) -> None:
    # Given
    worker_module = _worker_module()
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = writer.append_event(_event("signal-1"))
    sender = FakeTelegramSender(message_ids=["100"])
    second = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(worker_id="worker-2", clock=ManualClock()),
    )
    observed = []
    sender.during_send = lambda: observed.append(second.tick())
    first = worker_module.HermesDeliveryWorker(
        store=store,
        sender=sender,
        settings=worker_module.HermesDeliveryWorkerSettings(worker_id="worker-1", clock=ManualClock()),
    )

    # When
    result = first.tick()

    # Then
    assert result.status is worker_module.HermesDeliveryTickStatus.ACKNOWLEDGED
    assert observed[0].status is worker_module.HermesDeliveryTickStatus.BUSY
    assert len(store.attempts()) == 1


def test_delivery_daemon_starts_only_once_per_process(tmp_path: Path, monkeypatch) -> None:
    # Given
    worker_module = _worker_module()
    sender = FakeTelegramSender(message_ids=[])

    class FakeThread:
        def __init__(self, *, target, args, name, daemon) -> None:
            _ = (target, args, name, daemon)
            self.started = False

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started

    monkeypatch.setattr(worker_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(worker_module._DAEMON_STATE, "thread", None)

    # When
    first = worker_module.start_delivery_daemon(tmp_path / "delivery.sqlite3", sender)
    second = worker_module.start_delivery_daemon(tmp_path / "delivery.sqlite3", sender)

    # Then
    assert first is True
    assert second is False
    assert worker_module.delivery_daemon_status() == "running"


def _event(
    source_event_id: str,
    *,
    root_delivery_id: str | None = None,
    kind: HermesDeliveryKind = HermesDeliveryKind.ACTIONABLE,
    max_attempts: int = 3,
):
    return build_hermes_delivery_event(
        kind=kind,
        source_event_id=source_event_id,
        market_id="us_equities",
        lane_id="intraday_momentum",
        occurred_at=AT,
        payload_sha256="a" * 64,
        rendered_text=f"delivery for {source_event_id}",
        root_delivery_id=root_delivery_id,
        max_attempts=max_attempts,
    )


def _worker_module():
    return _load_module("trading_agent_hermes_delivery_worker", "delivery_worker.py", package=False)


def _load_module(name: str, filename: str, *, package: bool):
    if name in sys.modules:
        return sys.modules[name]
    root = Path(__file__).parents[1] / "integrations" / "hermes" / "trading-agent"
    options = {"submodule_search_locations": [str(root)]} if package else {}
    specification = importlib.util.spec_from_file_location(name, root / filename, **options)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module
