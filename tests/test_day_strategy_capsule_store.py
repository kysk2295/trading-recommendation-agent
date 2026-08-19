from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.day_strategy_capsule_support import (
    bar as _bar,
)
from tests.day_strategy_capsule_support import (
    builtin_capsule as _builtin_capsule,
)
from tests.day_strategy_capsule_support import (
    no_signal_source as _no_signal_source,
)
from tests.day_strategy_capsule_support import (
    proposal as _proposal,
)
from tests.strategy_research_contract_fixtures import hypothesis
from tests.test_day_research_attempt_binding import (
    SHA_A,
    _attempt,
    _binding,
    _family,
    _manifest,
    _version,
)
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import CapsuleArtifactKind
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_types import AttemptStatus


def _prepared_store(
    path: Path,
    status: AttemptStatus = AttemptStatus.SUCCEEDED,
    capsule_cadence: str | None = None,
) -> tuple[ExperimentLedgerStore, DayStrategyCapsuleRequest]:
    store = ExperimentLedgerStore(path)
    family = _family()
    version = _version(family)
    attempt = _attempt(0, status)
    binding = _binding(attempt, version)
    base = _builtin_capsule()
    request = DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=version.market_id,
        artifact_kind=base.artifact_kind,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=SHA_A,
        generated_artifact_id=None,
        evaluation_cadence=(version.evaluation_cadence if capsule_cadence is None else capsule_cadence),
        evidence_schema=base.evidence_schema,
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule=base.target_rule,
        cost_model=version.cost_model,
        slippage_model_id=base.slippage_model_id,
        resource_limits=base.resource_limits,
        risk_policy_ref=base.risk_policy_ref,
        protocol_version=1,
        protocol_sha256=version.protocol_sha256,
        evaluator_sha256=base.evaluator_sha256,
        published_at=binding.bound_at + dt.timedelta(minutes=1),
        authority_ceiling=base.authority_ceiling,
    )
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    return store, request


def test_verified_capsule_publication_is_idempotent_and_queryable(tmp_path: Path) -> None:
    # Given: a successful same-market attempt binding and exact artifact declaration.
    store, request = _prepared_store(tmp_path / "ledger.sqlite3")

    # When: the capsule is published and replayed.
    capsule, created = publish_day_strategy_capsule(store, request)
    replayed, replay_created = publish_day_strategy_capsule(store, request)

    # Then: deterministic readers expose one validated immutable capsule.
    assert created is True
    assert replay_created is False
    assert replayed == capsule
    stored = store.reader().day_strategy_capsule(capsule.capsule_id)
    assert stored is not None and stored.capsule == capsule
    assert tuple(item.capsule for item in store.day_strategy_capsules(capsule.market_id)) == (capsule,)


def test_real_generated_builder_capability_publishes_and_replays(tmp_path: Path) -> None:
    # Given: a real generated artifact, derived host bundles, sandbox receipt, and matching ledger parents.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    artifact_store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = artifact_store.publish(_proposal(_no_signal_source()))
    source_sha256 = published.artifact.payload.source_sha256
    family = _family()
    version_base = _version(family, code_sha256=source_sha256)
    version_payload = version_base.model_dump(mode="python") | {
        "hypothesis_version_id": "",
        "protocol_sha256": generated_protocol_bundle_sha256(),
    }
    version = version_base.model_validate(
        version_payload | {"hypothesis_version_id": version_base.canonical_id_for(version_payload)}
    )
    registered_hypothesis = hypothesis().model_copy(update={"code_sha256": source_sha256})
    manifest = PreregistrationManifest.from_hypothesis(
        registered_hypothesis,
        preregistered_at=registered_hypothesis.created_at,
    )
    attempt = _attempt(0, AttemptStatus.SUCCEEDED).model_copy(
        update={
            "hypothesis_id": registered_hypothesis.hypothesis_id,
            "code_sha256": source_sha256,
            "artifact_refs": (f"artifact://safe/{source_sha256}",),
        }
    )
    binding = _binding(
        attempt,
        version,
        artifact_ref=f"artifact://safe/{source_sha256}",
    )
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    with store.writer() as writer:
        assert writer.register_strategy_research(manifest)
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    limits = _builtin_capsule().resource_limits
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())
    request = DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=version.market_id,
        artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=source_sha256,
        generated_artifact_id=published.artifact.artifact_id,
        evaluation_cadence=version.evaluation_cadence,
        evidence_schema=("completed_bar_v1",),
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule="host_projects_preregistered_targets",
        cost_model=version.cost_model,
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=generated_protocol_bundle_sha256(),
        evaluator_sha256=generated_evaluator_bundle_sha256(),
        published_at=binding.bound_at + dt.timedelta(minutes=1),
        authority_ceiling=_builtin_capsule().authority_ceiling,
        generated_verification=GeneratedCapsuleVerification(
            artifact_store,
            sandbox,
            (_bar(),),
        ),
    )

    # When: the host build-and-persist boundary publishes twice.
    capsule, created = publish_day_strategy_capsule(store, request)
    replayed, replay_created = publish_day_strategy_capsule(store, request)

    # Then: publication is exact and idempotent.
    stored = store.day_strategy_capsule(capsule.capsule_id)
    assert created is True
    assert replay_created is False
    assert replayed == capsule
    assert version.protocol_sha256 == capsule.protocol_sha256
    assert capsule.protocol_sha256 == generated_protocol_bundle_sha256()
    assert capsule.artifact_sha256 == source_sha256
    assert stored is not None and stored.capsule == capsule


def test_capsule_requires_successful_exact_attempt_binding(tmp_path: Path) -> None:
    # Given: a terminal but failed attempt binding.
    store, request = _prepared_store(tmp_path / "ledger.sqlite3", AttemptStatus.FAILED)

    # When/Then: publication fails closed and leaves no capsule row.
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = publish_day_strategy_capsule(store, request)
    assert store.day_strategy_capsules() == ()


def test_reader_rejects_tampered_index_and_noncanonical_payload(tmp_path: Path) -> None:
    # Given: a stored capsule whose append-only trigger is deliberately removed for corruption simulation.
    store, request = _prepared_store(tmp_path / "ledger.sqlite3")
    capsule, _ = publish_day_strategy_capsule(store, request)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER day_strategy_capsules_no_update")
        connection.execute(
            "UPDATE day_strategy_capsules SET market_id=?,payload_json=? WHERE capsule_id=?",
            (
                "kr_equities",
                canonical_experiment_ledger_json(capsule) + " ",
                capsule.capsule_id,
            ),
        )

    # When/Then: the read boundary validates both canonical payload and indexed columns.
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.day_strategy_capsules()


def test_capsule_rejects_version_declaration_mismatch(tmp_path: Path) -> None:
    # Given: a capsule whose cadence differs from its same-market hypothesis version.
    store, mismatched = _prepared_store(
        tmp_path / "ledger.sqlite3",
        capsule_cadence="session_close_only",
    )

    # When/Then: parent declaration coherence fails before insertion.
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = publish_day_strategy_capsule(store, mismatched)
    assert store.day_strategy_capsules() == ()


def test_missing_store_is_empty_and_reader_connection_is_query_only(tmp_path: Path) -> None:
    # Given: an uninitialized ledger path.
    store = ExperimentLedgerStore(tmp_path / "missing.sqlite3")

    # When/Then: deterministic queries are empty and an initialized reader cannot mutate.
    assert store.day_strategy_capsules() == ()
    assert store.day_strategy_capsule("a" * 64) is None
    initialized, _ = _prepared_store(tmp_path / "initialized.sqlite3")
    with (
        initialized.reader()._reader_connection() as connection,
        pytest.raises(
            sqlite3.OperationalError,
            match="readonly",
        ),
    ):
        connection.execute("INSERT INTO day_strategy_capsules VALUES ('x','x','us_equities','x','x')")
