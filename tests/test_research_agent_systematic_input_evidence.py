from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.research_agent_systematic_input_fixtures import (
    SystematicInputGraphFixture,
    replace_model_artifact,
    write_systematic_input_graph,
)
from trading_agent.data_capability_models import DataHealthState
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogReceipt,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingReceipt,
)
from trading_agent.research_agent_systematic_input_evidence import (
    MAX_SYSTEMATIC_INPUT_BARS,
    MAX_SYSTEMATIC_INPUT_RSS_GIB,
    MAX_SYSTEMATIC_INPUT_SESSIONS,
    SystematicInputEvidenceError,
    verify_systematic_input_evidence_graph,
)


def test_verifier_returns_strict_facts_for_one_valid_connected_graph(
    tmp_path: Path,
) -> None:
    # Given: artifacts emitted by the strict catalog and input-binding producers.
    graph = write_systematic_input_graph(tmp_path / "artifacts")

    # When: the query-only evidence verifier resolves the graph.
    facts = verify_systematic_input_evidence_graph(graph.root)

    # Then: every activation fact is content-addressed and policy-bounded.
    assert facts.input_csv_path == graph.input_csv_path.resolve()
    assert facts.input_csv_sha256 == facts.input_sha256
    assert facts.selected_session_dates == (graph_date := facts.selected_session_dates[0],)
    assert graph_date.isoformat() == "2026-07-14"
    assert facts.bar_count == 384
    assert facts.max_sessions == MAX_SYSTEMATIC_INPUT_SESSIONS
    assert facts.max_bars == MAX_SYSTEMATIC_INPUT_BARS
    assert facts.rss_limit_gib == MAX_SYSTEMATIC_INPUT_RSS_GIB
    assert facts.dataset_receipt_path == graph.dataset_receipt_path.resolve()
    assert facts.catalog_receipt_path == graph.catalog_receipt_path.resolve()
    assert facts.input_binding_receipt_path == graph.input_binding_receipt_path.resolve()
    assert facts.foundation_path == graph.foundation_path.resolve()
    for path, digest in (
        (facts.input_csv_path, facts.input_csv_sha256),
        (facts.dataset_receipt_path, facts.dataset_receipt_sha256),
        (facts.catalog_receipt_path, facts.catalog_receipt_sha256),
        (facts.input_binding_receipt_path, facts.input_binding_receipt_sha256),
        (facts.foundation_path, facts.foundation_sha256),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_verifier_rejects_wrong_dataset_receipt_filename(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    wrong = graph.dataset_receipt_path.with_name("wrong-dataset-receipt.json")
    graph.dataset_receipt_path.rename(wrong)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_tampered_csv(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    graph.input_csv_path.write_text(
        graph.input_csv_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_wrong_catalog_receipt_edge(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    catalog = IntradayResearchDatasetCatalogReceipt.model_validate_json(
        graph.catalog_receipt_path.read_bytes()
    ).model_copy(update={"dataset_receipt_name": "another-receipt.json"})
    catalog_path, _ = replace_model_artifact(
        graph.catalog_receipt_path,
        catalog,
        "intraday_research_catalog_",
    )
    graph = replace(graph, catalog_receipt_path=catalog_path)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_disconnected_binding_edge(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    binding = IntradayResearchInputBindingReceipt.model_validate_json(
        graph.input_binding_receipt_path.read_bytes()
    ).model_copy(update={"dataset_receipt_sha256": "f" * 64})
    binding_path, _ = replace_model_artifact(
        graph.input_binding_receipt_path,
        binding,
        "intraday_research_input_binding_",
    )
    graph = replace(graph, input_binding_receipt_path=binding_path)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_fixture_foundation(tmp_path: Path) -> None:
    graph = _rewrite_foundation(tmp_path / "artifacts", provider="fixture")

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_non_ready_foundation(tmp_path: Path) -> None:
    graph = _rewrite_foundation(tmp_path / "artifacts", ready=False)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_graph_under_examples_path(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "examples" / "artifacts")

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_multiple_connected_graphs(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _ = write_systematic_input_graph(root / "one")
    _ = write_systematic_input_graph(root / "two")

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(root)


def test_verifier_rejects_mode_not_600(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    graph.catalog_receipt_path.chmod(0o644)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_symlink_artifact(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    target = graph.catalog_receipt_path.with_name("catalog-target.json")
    graph.catalog_receipt_path.rename(target)
    graph.catalog_receipt_path.symlink_to(target)

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_is_query_only(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "artifacts")
    before = {
        path.relative_to(graph.root): path.read_bytes()
        for path in graph.root.rglob("*")
        if path.is_file()
    }

    _ = verify_systematic_input_evidence_graph(graph.root)

    after = {
        path.relative_to(graph.root): path.read_bytes()
        for path in graph.root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_verifier_never_admits_outputs_live_sessions_directly(tmp_path: Path) -> None:
    graph = write_systematic_input_graph(tmp_path / "outputs" / "live_sessions")

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(graph.root)


def test_verifier_rejects_zero_candidate_graph(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    with pytest.raises(SystematicInputEvidenceError):
        _ = verify_systematic_input_evidence_graph(root)


def _rewrite_foundation(
    root: Path,
    *,
    provider: str | None = None,
    ready: bool = True,
) -> SystematicInputGraphFixture:
    graph = write_systematic_input_graph(root)
    foundation = DataFoundationManifest.model_validate_json(graph.foundation_path.read_bytes())
    source_id = foundation.capabilities[0].source_id
    if provider is not None:
        source_id = source_id.model_copy(update={"provider": provider})
    capability = foundation.capabilities[0].model_copy(
        update={
            "source_id": source_id,
            "health_state": DataHealthState.COMPLETE if ready else DataHealthState.FAILED,
        }
    )
    entitlement = foundation.entitlements[0].model_copy(update={"source_id": source_id})
    requirement = foundation.requirements[0].model_copy(update={"primary_source_id": source_id})
    foundation = foundation.model_copy(
        update={
            "capabilities": (capability,),
            "entitlements": (entitlement,),
            "requirements": (requirement,),
        }
    )
    foundation_path, foundation_sha = replace_model_artifact(
        graph.foundation_path,
        foundation,
        f"intraday_data_foundation_{foundation.strategy_lane.strategy_id}_",
    )
    binding = IntradayResearchInputBindingReceipt.model_validate_json(
        graph.input_binding_receipt_path.read_bytes()
    ).model_copy(update={"foundation_sha256s": (foundation_sha,)})
    binding_path, _ = replace_model_artifact(
        graph.input_binding_receipt_path,
        binding,
        "intraday_research_input_binding_",
    )
    return replace(
        graph,
        foundation_path=foundation_path,
        input_binding_receipt_path=binding_path,
    )
