from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pydantic import ValidationError

from trading_agent.strategy_lab_keys import (
    canonical_strategy_lab_json,
    strategy_lab_node_id,
    strategy_lab_protocol_id,
)
from trading_agent.strategy_lab_models import (
    StrategyLabId,
    StrategyLabProtocol,
    StrategyLabTraceNode,
)


@dataclass(frozen=True, slots=True)
class StrategyLabLedgerError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def read_strategy_lab_protocols(
    connection: sqlite3.Connection,
    lab_id: StrategyLabId | None = None,
) -> tuple[StrategyLabProtocol, ...]:
    if lab_id is None:
        rows: list[tuple[str, str, str, str, str]] = connection.execute(
            """SELECT protocol_id,lab_id,hypothesis_id,dataset_id,payload_json
            FROM strategy_lab_protocols ORDER BY rowid"""
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT protocol_id,lab_id,hypothesis_id,dataset_id,payload_json
            FROM strategy_lab_protocols WHERE lab_id=? ORDER BY rowid""",
            (lab_id.value,),
        ).fetchall()
    return tuple(_protocol_from_row(row) for row in rows)


def read_strategy_lab_trace(
    connection: sqlite3.Connection,
    lab_id: StrategyLabId,
) -> tuple[StrategyLabTraceNode, ...]:
    rows: list[tuple[str, str, int, str | None, str, str, str]] = connection.execute(
        """SELECT node_id,lab_id,iteration,parent_node_id,protocol_id,outcome,payload_json
        FROM strategy_lab_trace_nodes WHERE lab_id=? ORDER BY iteration""",
        (lab_id.value,),
    ).fetchall()
    nodes = tuple(_node_from_row(row) for row in rows)
    previous: StrategyLabTraceNode | None = None
    for expected_iteration, node in enumerate(nodes, start=1):
        expected_parent = None if previous is None else previous.node_id
        if (
            node.body.iteration != expected_iteration
            or node.body.parent_node_id != expected_parent
        ):
            raise StrategyLabLedgerError("strategy_lab_trace_chain_invalid")
        previous = node
    return nodes


def register_strategy_lab_protocol(
    connection: sqlite3.Connection,
    protocol: StrategyLabProtocol,
) -> bool:
    _require_protocol_hash(protocol)
    existing = connection.execute(
        "SELECT payload_json FROM strategy_lab_protocols WHERE protocol_id=?",
        (protocol.protocol_id,),
    ).fetchone()
    payload = canonical_strategy_lab_json(protocol)
    if existing is not None:
        if existing == (payload,):
            return False
        raise StrategyLabLedgerError("strategy_lab_protocol_identity_conflict")
    conflict = connection.execute(
        """SELECT protocol_id FROM strategy_lab_protocols
        WHERE hypothesis_id=? OR (lab_id=? AND dataset_id=?)""",
        (
            protocol.body.hypothesis.hypothesis_id,
            protocol.body.lab_id.value,
            protocol.body.dataset_id,
        ),
    ).fetchone()
    if conflict is not None:
        raise StrategyLabLedgerError("strategy_lab_protocol_identity_conflict")
    _ = connection.execute(
        "INSERT INTO strategy_lab_protocols VALUES (?,?,?,?,?)",
        (
            protocol.protocol_id,
            protocol.body.lab_id.value,
            protocol.body.hypothesis.hypothesis_id,
            protocol.body.dataset_id,
            payload,
        ),
    )
    return True


def append_strategy_lab_trace_node(
    connection: sqlite3.Connection,
    node: StrategyLabTraceNode,
) -> bool:
    _require_node_hash(node)
    existing = connection.execute(
        "SELECT payload_json FROM strategy_lab_trace_nodes WHERE node_id=?",
        (node.node_id,),
    ).fetchone()
    payload = canonical_strategy_lab_json(node)
    if existing is not None:
        if existing == (payload,):
            return False
        raise StrategyLabLedgerError("strategy_lab_node_identity_conflict")
    protocol_row = connection.execute(
        "SELECT payload_json FROM strategy_lab_protocols WHERE protocol_id=?",
        (node.body.protocol_id,),
    ).fetchone()
    if protocol_row is None:
        raise StrategyLabLedgerError("strategy_lab_protocol_missing")
    protocol = _protocol_from_payload(protocol_row[0])
    if (
        protocol.body.lab_id is not node.body.lab_id
        or node.body.result.protocol_id != protocol.protocol_id
    ):
        raise StrategyLabLedgerError("strategy_lab_node_protocol_mismatch")
    trace = read_strategy_lab_trace(connection, node.body.lab_id)
    expected_iteration = len(trace) + 1
    expected_parent = None if not trace else trace[-1].node_id
    if (
        node.body.iteration != expected_iteration
        or node.body.parent_node_id != expected_parent
    ):
        raise StrategyLabLedgerError("strategy_lab_trace_chain_invalid")
    _ = connection.execute(
        "INSERT INTO strategy_lab_trace_nodes VALUES (?,?,?,?,?,?,?)",
        (
            node.node_id,
            node.body.lab_id.value,
            node.body.iteration,
            node.body.parent_node_id,
            node.body.protocol_id,
            node.body.result.outcome.value,
            payload,
        ),
    )
    return True


def _protocol_from_row(row: tuple[str, str, str, str, str]) -> StrategyLabProtocol:
    protocol_id, lab_id, hypothesis_id, dataset_id, payload = row
    protocol = _protocol_from_payload(payload)
    if (
        protocol.protocol_id != protocol_id
        or protocol.body.lab_id.value != lab_id
        or protocol.body.hypothesis.hypothesis_id != hypothesis_id
        or protocol.body.dataset_id != dataset_id
    ):
        raise StrategyLabLedgerError("stored_strategy_lab_protocol_invalid")
    return protocol


def _protocol_from_payload(payload: str) -> StrategyLabProtocol:
    try:
        protocol = StrategyLabProtocol.model_validate_json(payload)
    except (ValidationError, ValueError):
        raise StrategyLabLedgerError("stored_strategy_lab_protocol_invalid") from None
    _require_protocol_hash(protocol)
    return protocol


def _node_from_row(
    row: tuple[str, str, int, str | None, str, str, str],
) -> StrategyLabTraceNode:
    node_id, lab_id, iteration, parent_node_id, protocol_id, outcome, payload = row
    try:
        node = StrategyLabTraceNode.model_validate_json(payload)
    except (ValidationError, ValueError):
        raise StrategyLabLedgerError("stored_strategy_lab_node_invalid") from None
    _require_node_hash(node)
    if (
        node.node_id != node_id
        or node.body.lab_id.value != lab_id
        or node.body.iteration != iteration
        or node.body.parent_node_id != parent_node_id
        or node.body.protocol_id != protocol_id
        or node.body.result.outcome.value != outcome
    ):
        raise StrategyLabLedgerError("stored_strategy_lab_node_invalid")
    return node


def _require_protocol_hash(protocol: StrategyLabProtocol) -> None:
    if strategy_lab_protocol_id(protocol) != protocol.protocol_id:
        raise StrategyLabLedgerError("strategy_lab_protocol_hash_invalid")


def _require_node_hash(node: StrategyLabTraceNode) -> None:
    if strategy_lab_node_id(node) != node.node_id:
        raise StrategyLabLedgerError("strategy_lab_node_hash_invalid")


__all__ = (
    "StrategyLabLedgerError",
    "append_strategy_lab_trace_node",
    "read_strategy_lab_protocols",
    "read_strategy_lab_trace",
    "register_strategy_lab_protocol",
)
