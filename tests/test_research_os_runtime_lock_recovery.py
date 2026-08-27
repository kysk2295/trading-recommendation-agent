from __future__ import annotations

import anyio
import pytest

from tests.test_research_agent_service_cli import _config
from trading_agent.hermes_delivery_errors import HermesDeliveryWriterLeaseUnavailableError
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_os_runtime import run_research_os_forever


def test_forever_loop_retries_after_transient_hermes_writer_collision(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    calls = 0
    waits: list[float] = []

    class ReadyRuntime:
        def close(self) -> None:
            pass

    class StopAfterRetry(RuntimeError):
        pass

    def collide_once(
        _config: ResearchAgentServiceConfig,
        _now,
        *,
        operation: str,
    ):
        nonlocal calls
        assert operation == "run"
        calls += 1
        if calls == 1:
            raise HermesDeliveryWriterLeaseUnavailableError
        raise StopAfterRetry

    async def observe_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("trading_agent.research_os_runtime.build_service_runtime", lambda _: ReadyRuntime())
    monkeypatch.setattr("trading_agent.research_os_runtime.run_research_os_tick", collide_once)
    monkeypatch.setattr("trading_agent.research_os_runtime.anyio.sleep", observe_wait)

    with pytest.raises(StopAfterRetry):
        anyio.run(run_research_os_forever, config)

    assert calls == 2
    assert waits == [30.0]
