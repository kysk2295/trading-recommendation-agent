from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.browser_social_evidence_support import evidence
from trading_agent._autonomous_supervisor_wire import tools_wire
from trading_agent.autonomous_browser_tools import BrowserToolServices
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_supervisor_service import build_foundation_tool_runtime
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskId
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext

NOW = dt.datetime(2026, 8, 27, 3, 30, tzinfo=dt.UTC)


def test_browser_tools_are_role_scoped_and_total_tool_count_stays_bounded(tmp_path: Path) -> None:
    # Given: foundation stores and configured local-only browser services.
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    browser = BrowserToolServices(tmp_path / "gateway.sock", tmp_path / "browser-evidence.sqlite3")

    # When: the composed runtime exposes each role's permitted tools.
    runtime = build_foundation_tool_runtime(tasks, memories, browser=browser)

    # Then: browser authority is role-scoped, bounded, and absent from trading.
    assert runtime.allowed_tools(AutonomousAgentRole.MARKET_OBSERVER) == (
        "browser.capture",
        "browser.follow",
        "browser.open",
        "browser.read",
        "browser.search",
        "browser.status",
        "evidence.read",
        "memory.search",
        "social.evidence.search",
        "task.history",
    )
    assert len(runtime.allowed_tools(AutonomousAgentRole.MARKET_OBSERVER)) <= 16
    assert "browser.status()" in runtime.allowed_tool_signatures(AutonomousAgentRole.MARKET_OBSERVER)
    assert "browser.search(query)" in runtime.allowed_tool_signatures(AutonomousAgentRole.MARKET_OBSERVER)
    assert runtime.allowed_tools(AutonomousAgentRole.TRADING) == (
        "evidence.read",
        "memory.search",
        "task.history",
    )
    assert tools_wire(runtime).worker_modules == frozenset(
        {
            "trading_agent.autonomous_browser_tool_actions",
            "trading_agent.autonomous_browser_tools",
            "trading_agent.autonomous_supervisor_service",
        }
    )
    tasks.close()
    memories.close()


def test_browser_read_appends_evidence_before_returning_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given: a local gateway client returns one bounded visible browser observation.
    from trading_agent import autonomous_browser_tool_results as browser_results
    from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
    from trading_agent.local_browser_protocol import (
        BrowserAction,
        BrowserPageObservation,
        BrowserResponse,
        BrowserVisibleLink,
    )

    class FixtureGatewayClient:
        def __init__(self, _path: Path, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 20.0

        def request(self, request):
            return BrowserResponse(
                request_id=request.request_id,
                action=BrowserAction.READ,
                observation=BrowserPageObservation(
                    target_id="target-1",
                    url="https://example.com/research",
                    title="Bounded research page",
                    visible_text="A bounded visible observation for durable evidence.",
                    links=(BrowserVisibleLink(label="Related", url="https://example.org/related"),),
                    captured_at=NOW,
                ),
            )

    monkeypatch.setattr(browser_results, "LocalBrowserGatewayClient", FixtureGatewayClient)
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    browser = BrowserToolServices(tmp_path / "gateway.sock", tmp_path / "browser-evidence.sqlite3")
    runtime = build_foundation_tool_runtime(tasks, memories, browser=browser)
    context = AutonomousToolExecutionContext(
        task_id=AutonomousTaskId("a" * 64),
        agent_family_id="market_context",
        market_scope="kr_equities",
    )

    # When: the Market Observer reads the current browser page.
    observation = runtime.dispatch(
        AutonomousAgentRole.MARKET_OBSERVER,
        AutonomousToolCall(
            tool_name="browser.read",
            args=AutonomousToolArguments({"target_id": "target-1"}),
            reason="Persist the bounded browser observation before continuing research.",
        ),
        context,
    )

    # Then: successful output identifies evidence already appended to the separate browser store.
    payload = json.loads(observation.bounded_json)
    assert payload["status"] == "ok"
    assert payload["browser_receipt_id"] == payload["evidence_id"] or len(payload["browser_receipt_id"]) == 64
    persisted = BrowserSocialEvidenceStore(browser.evidence_database).get(payload["evidence_id"])
    assert persisted is not None
    assert persisted.browser_receipt_id == payload["browser_receipt_id"]
    assert persisted.normalized_url == "https://example.com/research"
    tasks.close()
    memories.close()


def test_browser_read_empty_visible_text_returns_a_durable_blocked_receipt_without_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given: a visual-only browser page has a receipt but no visible text.
    from trading_agent import autonomous_browser_tool_results as browser_results
    from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
    from trading_agent.local_browser_protocol import BrowserAction, BrowserPageObservation, BrowserResponse

    class VisualOnlyGatewayClient:
        def __init__(self, _path: Path, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 20.0

        def request(self, request):
            return BrowserResponse(
                request_id=request.request_id,
                action=BrowserAction.READ,
                observation=BrowserPageObservation(
                    target_id="visual-target",
                    url="https://example.com/visual-only",
                    title="Visual-only page",
                    visible_text="",
                    captured_at=NOW,
                ),
            )

    monkeypatch.setattr(browser_results, "LocalBrowserGatewayClient", VisualOnlyGatewayClient)
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    browser = BrowserToolServices(tmp_path / "gateway.sock", tmp_path / "browser-evidence.sqlite3")
    runtime = build_foundation_tool_runtime(tasks, memories, browser=browser)
    context = AutonomousToolExecutionContext(
        task_id=AutonomousTaskId("c" * 64),
        agent_family_id="market_context",
        market_scope="kr_equities",
    )

    # When: the current page is read through the authorized browser binding.
    observation = runtime.dispatch(
        AutonomousAgentRole.MARKET_OBSERVER,
        AutonomousToolCall(
            tool_name="browser.read",
            args=AutonomousToolArguments({"target_id": "visual-target"}),
            reason="Record a blocked visual-only browser observation without inventing text.",
        ),
        context,
    )

    # Then: the durable tool observation retains receipt lineage and does not create social evidence.
    payload = json.loads(observation.bounded_json)
    assert payload["captured_at"] == NOW.isoformat()
    assert payload["normalized_url"] == "https://example.com/visual-only"
    assert payload["reason"] == "browser_visible_text_unavailable"
    assert payload["status"] == "blocked"
    assert payload["target_id"] == "visual-target"
    assert payload["title"] == "Visual-only page"
    assert "evidence_id" not in payload
    assert len(payload["browser_receipt_id"]) == 64
    assert BrowserSocialEvidenceStore(browser.evidence_database).search("visual-only") == ()
    tasks.close()
    memories.close()


def test_browser_read_gateway_block_returns_a_blocked_receipt(tmp_path: Path, monkeypatch) -> None:
    # Given: the gateway reports a stable bot/navigation block for a read request.
    from trading_agent import autonomous_browser_tool_results as browser_results
    from trading_agent.local_browser_protocol import (
        BrowserAction,
        BrowserFailure,
        BrowserFailureReason,
        BrowserResponse,
    )

    class BlockedGatewayClient:
        def __init__(self, _path: Path, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 20.0

        def request(self, request):
            return BrowserResponse(
                request_id=request.request_id,
                action=BrowserAction.READ,
                status="error",
                failure=BrowserFailure(reason=BrowserFailureReason.NAVIGATION_BLOCKED),
            )

    monkeypatch.setattr(browser_results, "LocalBrowserGatewayClient", BlockedGatewayClient)
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    browser = BrowserToolServices(tmp_path / "gateway.sock", tmp_path / "browser-evidence.sqlite3")
    runtime = build_foundation_tool_runtime(tasks, memories, browser=browser)
    context = AutonomousToolExecutionContext(
        task_id=AutonomousTaskId("d" * 64),
        agent_family_id="market_context",
        market_scope="kr_equities",
    )

    # When: the Market Observer receives the blocked read receipt.
    observation = runtime.dispatch(
        AutonomousAgentRole.MARKET_OBSERVER,
        AutonomousToolCall(
            tool_name="browser.read",
            args=AutonomousToolArguments({"target_id": "blocked-target"}),
            reason="Record the bounded blocked browser receipt without bypassing the source.",
        ),
        context,
    )

    # Then: it remains an attributable blocked observation instead of a tool failure.
    payload = json.loads(observation.bounded_json)
    assert payload["reason"] == "browser_navigation_blocked"
    assert payload["status"] == "blocked"
    assert payload["target_id"] == "blocked-target"
    assert len(payload["browser_receipt_id"]) == 64
    tasks.close()
    memories.close()


def test_social_evidence_search_reads_prior_browser_evidence_without_gateway(tmp_path: Path) -> None:
    # Given: an existing browser-evidence record and a missing gateway socket.
    from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore

    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    browser = BrowserToolServices(tmp_path / "missing.sock", tmp_path / "browser-evidence.sqlite3")
    stored = evidence(excerpt="Semiconductor demand remains visible in the bounded source.")
    assert BrowserSocialEvidenceStore(browser.evidence_database).append(stored)
    runtime = build_foundation_tool_runtime(tasks, memories, browser=browser)
    context = AutonomousToolExecutionContext(
        task_id=AutonomousTaskId("b" * 64),
        agent_family_id="market_context",
        market_scope="kr_equities",
    )

    # When: Research searches durable browser evidence without opening Chrome.
    observation = runtime.dispatch(
        AutonomousAgentRole.RESEARCH,
        AutonomousToolCall(
            tool_name="social.evidence.search",
            args=AutonomousToolArguments({"query": "semiconductor"}),
            reason="Find prior bounded browser evidence before selecting another source.",
        ),
        context,
    )

    # Then: the canonical tool output exposes the pre-existing evidence only.
    assert json.loads(observation.bounded_json)["evidence"][0]["evidence_id"] == stored.evidence_id
    tasks.close()
    memories.close()


def test_browser_page_projection_stays_below_the_tool_result_cap() -> None:
    # Given: a protocol-bounded observation with escape-heavy visible page fields.
    from trading_agent.autonomous_browser_tool_results import canonical, page_payload
    from trading_agent.local_browser_protocol import BrowserPageObservation

    observation = BrowserPageObservation(
        target_id="가" * 256,
        url=f"https://example.com/{'가' * 2_000}",
        title="가" * 500,
        visible_text="가" * 12_000,
        captured_at=NOW,
    )

    # When: the browser-tool projection is prepared for canonical return.
    payload = canonical(
        {
            **page_payload(observation),
            "browser_receipt_id": "a" * 64,
            "evidence_id": "b" * 64,
            "screenshot_sha256": None,
            "status": "ok",
        }
    )

    # Then: the bounded projection remains within the supervisor's 16 KiB result contract.
    assert len(payload.encode("ascii")) <= 16_384
