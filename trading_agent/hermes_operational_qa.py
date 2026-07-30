from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_models import (
    HermesDeliveryFailure,
    HermesDeliveryKind,
    build_hermes_delivery_event,
)
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_query_service import HermesAgentQueryService
from trading_agent.private_stable_report import write_private_stable_report

_LEAK_PATTERN: Final = re.compile(
    r"(?i)(?:authorization|bearer|api[_-]?key|secret|token|\baccount(?:\b|[_ -])|"
    r"session[_ -]?(?:id|key|token)|(?:^|[\s=:])/[A-Za-z0-9._~/-]+)"
)
_FIXTURE_AT: Final = dt.datetime(2026, 7, 30, 15, 0, tzinfo=dt.UTC)
_FIXTURE_INSTRUMENT: Final = "QA-LOCAL"
_CONTROLLED_PROVIDER_MUTATIONS: Final[tuple[str, ...]] = ()
type RestartAggregate = tuple[int, int, int, bool, bool, bool, bool, int, int]
type DeliveryAggregate = tuple[int, int, int, int]
type ExecutionAggregate = tuple[int, int, int, int]
type QueryAggregate = tuple[tuple[str, ...], int]
type JsonValue = str | int | bool | None | list[JsonValue] | dict[str, JsonValue]


class InvalidHermesOperationalQaError(ValueError):
    @override
    def __str__(self) -> str:
        return "Hermes operational QA input is invalid"


@dataclass(frozen=True, slots=True)
class HermesOperationalQaRequest:
    delivery_store: Path | None
    execution_store: Path | None
    output_root: Path
    observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class HermesOperationalQaResult:
    reconciliation_path: Path
    query_report_path: Path


@dataclass(frozen=True, slots=True)
class ControlledProviderOutage:
    fixture: str
    kind: str
    network_calls: int
    provider_mutation_count: int
    terminal: bool


def run_hermes_operational_qa(request: HermesOperationalQaRequest) -> HermesOperationalQaResult:
    _require_aware(request.observed_at)
    output_root = _safe_output_root(request.output_root)
    reader = _delivery_reader(request.delivery_store)
    _require_clean_summaries(reader)
    delivery = _delivery_aggregate(reader)
    execution = _execution_aggregate(request.execution_store)
    restart = _controlled_restart_aggregate()
    provider = _controlled_provider_outage()
    query = _query_aggregate(reader, request.observed_at)
    report = _report_data(restart, delivery, execution, provider, query)
    reconciliation_path = output_root / "acceptance" / "soak" / "restart_and_provider_fault_reconciliation.json"
    query_report_path = output_root / "acceptance" / "hermes" / "query_and_alert_qa.md"
    reconciliation = json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    markdown = _markdown_report(delivery, execution, provider, query)
    _require_clean_publication(reconciliation)
    _require_clean_publication(markdown)
    try:
        write_private_stable_report(reconciliation_path, reconciliation)
        write_private_stable_report(query_report_path, markdown)
    except ValueError as error:
        raise InvalidHermesOperationalQaError from error
    return HermesOperationalQaResult(reconciliation_path, query_report_path)


def _controlled_restart_aggregate() -> RestartAggregate:
    with tempfile.TemporaryDirectory(prefix="hermes-operational-qa-") as directory:
        store = HermesDeliveryStore(Path(directory) / "delivery.sqlite3")
        root = build_hermes_delivery_event(
            kind=HermesDeliveryKind.RESEARCH,
            source_event_id="controlled-restart-root",
            market_id="US",
            lane_id="controlled-fixture",
            occurred_at=_FIXTURE_AT,
            payload_sha256="1" * 64,
            rendered_text="controlled fixture root",
            agent_family="opportunity_manager",
            instrument_id=_FIXTURE_INSTRUMENT,
            status="controlled_fixture",
        )
        reply = build_hermes_delivery_event(
            kind=HermesDeliveryKind.RESEARCH,
            source_event_id="controlled-restart-reply",
            market_id="US",
            lane_id="controlled-fixture",
            occurred_at=_FIXTURE_AT + dt.timedelta(seconds=1),
            payload_sha256="2" * 64,
            rendered_text="controlled fixture reply",
            agent_family="day_trading",
            instrument_id=_FIXTURE_INSTRUMENT,
            root_delivery_id=root.delivery_id,
            status="controlled_fixture",
        )
        suppressed = build_hermes_delivery_event(
            kind=HermesDeliveryKind.INCIDENT,
            source_event_id="controlled-provider-outage",
            market_id="US",
            lane_id="controlled-fixture",
            occurred_at=_FIXTURE_AT + dt.timedelta(seconds=2),
            payload_sha256="3" * 64,
            rendered_text="controlled fixture provider outage",
            agent_family="market_context",
            instrument_id=_FIXTURE_INSTRUMENT,
            status="controlled_fixture",
        )
        with store.writer() as writer:
            for event in (root, reply, suppressed):
                if not writer.append_event(event).inserted:
                    raise InvalidHermesOperationalQaError
            first = writer.claim_next(worker_id="fixture-a", now=_FIXTURE_AT, lease_seconds=10)
            if first is None:
                raise InvalidHermesOperationalQaError
        restarted_store = HermesDeliveryStore(store.path)
        with restarted_store.writer() as writer:
            second = writer.claim_next(
                worker_id="fixture-b", now=_FIXTURE_AT + dt.timedelta(seconds=11), lease_seconds=10
            )
            if second is None or not writer.acknowledge(
                second,
                platform_message_id="fixture-root-message",
                acknowledged_at=_FIXTURE_AT + dt.timedelta(seconds=12),
            ):
                raise InvalidHermesOperationalQaError
            reply_claim = writer.claim_next(
                worker_id="fixture-c", now=_FIXTURE_AT + dt.timedelta(seconds=13), lease_seconds=10
            )
            if reply_claim is None or not writer.acknowledge(
                reply_claim,
                platform_message_id="fixture-reply-message",
                acknowledged_at=_FIXTURE_AT + dt.timedelta(seconds=14),
            ):
                raise InvalidHermesOperationalQaError
            suppression_claim = writer.claim_next(
                worker_id="fixture-d", now=_FIXTURE_AT + dt.timedelta(seconds=15), lease_seconds=10
            )
            if suppression_claim is None:
                raise InvalidHermesOperationalQaError
            _ = writer.fail(
                suppression_claim,
                HermesDeliveryFailure(
                    failed_at=_FIXTURE_AT + dt.timedelta(seconds=16),
                    reason="controlled_fixture_suppression",
                    retry_delay_seconds=0,
                    terminal=True,
                ),
            )
        events = restarted_store.events()
        acknowledgements = restarted_store.acknowledgements()
        dead_letters = restarted_store.dead_letters()
    terminal_count = len(acknowledgements) + len(dead_letters)
    return (
        terminal_count,
        len(events) - len({event.delivery_id for event in events}),
        len(events) - terminal_count,
        (
            reply_claim.lineage.root_delivery_id == root.delivery_id
            and reply_claim.lineage.root_platform_message_id == "fixture-root-message"
        ),
        second.attempt.attempt_number == first.attempt.attempt_number + 1,
        first.event.delivery_id == second.event.delivery_id,
        restarted_store is not store and restarted_store.path == store.path,
        sum(item.reason == "controlled_fixture_suppression" for item in dead_letters),
        len(events) - terminal_count,
    )


def _delivery_reader(path: Path | None) -> HermesDeliveryReader:
    if path is None:
        raise InvalidHermesOperationalQaError
    return HermesDeliveryStore(_safe_existing_file(path))


def _execution_aggregate(path: Path | None) -> ExecutionAggregate:
    if path is None:
        raise InvalidHermesOperationalQaError
    store = ExecutionStore(_safe_existing_file(path))
    if not store.is_initialized():
        raise InvalidHermesOperationalQaError
    try:
        return (
            sum(len(store.broker_events(item.intent_id)) for item in store.intents()),
            len(store.paper_mutation_events()),
            len(store.paper_mutation_intents()),
            len(store.intents()),
        )
    except (sqlite3.DatabaseError, ValueError):
        raise InvalidHermesOperationalQaError from None


def _delivery_aggregate(reader: HermesDeliveryReader) -> DeliveryAggregate:
    try:
        return len(reader.events()), len(reader.attempts()), len(reader.acknowledgements()), len(reader.dead_letters())
    except (sqlite3.DatabaseError, ValueError):
        raise InvalidHermesOperationalQaError from None


def _controlled_provider_outage() -> ControlledProviderOutage:
    return ControlledProviderOutage(
        fixture="controlled_fixture",
        kind="read_only_provider_outage",
        network_calls=0,
        provider_mutation_count=len(_CONTROLLED_PROVIDER_MUTATIONS),
        terminal=True,
    )


def _query_aggregate(reader: HermesDeliveryReader, observed_at: dt.datetime) -> QueryAggregate:
    result = HermesAgentQueryService(reader).query(_FIXTURE_INSTRUMENT, observed_at=observed_at)
    names = tuple(item.agent_family.value for item in result.opinions)
    if len(names) != 6 or len(set(names)) != 6 or result.blended_verdict is not None:
        raise InvalidHermesOperationalQaError
    return names, len(names)


def _require_clean_summaries(reader: HermesDeliveryReader) -> None:
    try:
        summaries = tuple(event.rendered_text for event in reader.events())
    except (sqlite3.DatabaseError, ValueError):
        raise InvalidHermesOperationalQaError from None
    if any(_LEAK_PATTERN.search(summary) is not None for summary in summaries):
        raise InvalidHermesOperationalQaError


def _report_data(
    restart: RestartAggregate,
    delivery: DeliveryAggregate,
    execution: ExecutionAggregate,
    provider: ControlledProviderOutage,
    query: QueryAggregate,
) -> dict[str, JsonValue]:
    return {
        "controlled_fixture": True,
        "delivery": {
            "event_count": delivery[0],
            "attempt_count": delivery[1],
            "acknowledgement_count": delivery[2],
            "dead_letter_count": delivery[3],
        },
        "execution": {
            "broker_event_count": execution[0],
            "mutation_event_count": execution[1],
            "mutation_intent_count": execution[2],
            "order_intent_count": execution[3],
        },
        "provider_incident": {
            "fixture": provider.fixture,
            "kind": provider.kind,
            "network_calls": provider.network_calls,
            "provider_mutation_count": provider.provider_mutation_count,
            "terminal": provider.terminal,
        },
        "query": {"blended_verdict": None, "family_count": query[1], "family_names": list(query[0])},
        "real_session": False,
        "restart": {
            "acknowledged_or_terminal_count": restart[0],
            "duplicate_count": restart[1],
            "omission_count": restart[2],
            "reply_lineage_verified": restart[3],
            "retry_after_expired_claim": restart[4],
            "same_delivery_identity": restart[5],
            "store_reopened": restart[6],
            "suppression_terminal_count": restart[7],
            "unaccounted_count": restart[8],
        },
        "scenario": "controlled_fixture",
    }


def _markdown_report(
    delivery: DeliveryAggregate,
    execution: ExecutionAggregate,
    provider: ControlledProviderOutage,
    query: QueryAggregate,
) -> str:
    return "\n".join(
        (
            "# Hermes query and alert QA",
            "",
            "- scenario: controlled_fixture (never real session)",
            f"- provider incident: {provider.kind} fixture={provider.fixture} terminal=true network_calls=0",
            f"- provider mutation count: {provider.provider_mutation_count} (controlled fixture only)",
            f"- separate family count: {query[1]}",
            f"- family names: {', '.join(query[0])}",
            "- blended verdict: none",
            f"- delivery aggregate counts: {delivery[0]}/{delivery[1]}/{delivery[2]}/{delivery[3]}",
            f"- execution aggregate counts: {execution[3]}/{execution[0]}/{execution[2]}/{execution[1]}",
            "- outbound summary leak count: 0",
            "- generated report leak count: 0",
            "",
        )
    )


def _require_aware(value: dt.datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidHermesOperationalQaError


def _safe_existing_file(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise InvalidHermesOperationalQaError from error
    if absolute != resolved or not resolved.is_file() or resolved.is_symlink():
        raise InvalidHermesOperationalQaError
    return resolved


def _safe_output_root(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    existing = tuple(
        component for component in (absolute, *absolute.parents) if component.exists() or component.is_symlink()
    )
    if any(component.is_symlink() for component in existing):
        raise InvalidHermesOperationalQaError
    return absolute


def _require_clean_publication(content: str) -> None:
    if _LEAK_PATTERN.search(content) is not None:
        raise InvalidHermesOperationalQaError
