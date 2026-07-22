from __future__ import annotations

import ast
import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_experiment_ledger_store import (
    EFFECTIVE_DATE,
    ORB_SCOPE,
    _authorized_champion_transition,
    _hypothesis,
    _lifecycle_registration,
    _lifecycle_transition,
    _strategy_authority_binding,
    _version,
)
from trading_agent.experiment_ledger_keys import (
    hypothesis_registration_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_arm_authority import (
    LedgerHermesArmAuthorityConfig,
    LedgerHermesArmAuthorityResolver,
)
from trading_agent.hermes_arm_request import (
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import lane_account_binding
from trading_agent.lane_defaults import INTRADAY_MANIFEST
from trading_agent.lane_identity_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore

AT = dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC)
OWNER = "a" * 64
ACCOUNT = "c" * 64
SCOPE = HermesArmScope(session_id="XNYS-2026-07-22", lane_id=LaneId.INTRADAY_MOMENTUM)


@dataclass(frozen=True, slots=True)
class AuthorityFixture:
    repository: Path
    lane_registry: Path
    experiment_ledger: Path
    signing_key: Path
    arm_database: Path
    commit_sha: str


def test_ledger_authority_resolves_exact_clean_paper_champion(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=fixture.repository,
            lane_registry=fixture.lane_registry,
            experiment_ledger=fixture.experiment_ledger,
        )
    )

    # When
    authority = resolver.resolve(SCOPE)

    # Then
    assert authority.commit_sha == fixture.commit_sha
    assert authority.account_fingerprint == ACCOUNT
    assert authority.strategy_version == _version().strategy_version
    assert authority.champion_binding_key is not None


def test_ledger_authority_rejects_dirty_repository(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    (fixture.repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=fixture.repository,
            lane_registry=fixture.lane_registry,
            experiment_ledger=fixture.experiment_ledger,
        )
    )

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        _ = resolver.resolve(SCOPE)
    assert blocked.value.reason is HermesArmFailure.DIRTY_COMMIT


def test_cli_confirms_consumes_once_and_redacts_authority(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    common = (
        "--database",
        str(fixture.arm_database),
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--signing-key",
        str(fixture.signing_key),
    )

    # When
    prepared = _run_cli(
        "prepare",
        *common,
        "--owner-id-hash",
        OWNER,
        "--session-id",
        SCOPE.session_id,
        "--lane-id",
        SCOPE.lane_id.value,
    )
    prepared_payload = json.loads(prepared.stdout)
    confirmed = _run_cli(
        "confirm",
        *common,
        "--owner-id-hash",
        OWNER,
        "--request-id",
        prepared_payload["request_id"],
        "--confirmation",
        prepared_payload["confirmation"],
    )
    consumed = _run_cli(
        "consume",
        *common,
        "--request-id",
        prepared_payload["request_id"],
        "--session-id",
        SCOPE.session_id,
        "--lane-id",
        SCOPE.lane_id.value,
    )
    replay = _run_cli(
        "consume",
        *common,
        "--request-id",
        prepared_payload["request_id"],
        "--session-id",
        SCOPE.session_id,
        "--lane-id",
        SCOPE.lane_id.value,
        check=False,
    )

    # Then
    assert json.loads(confirmed.stdout)["result"] == "confirmed"
    assert json.loads(consumed.stdout)["result"] == "consumed"
    assert replay.returncode == 1
    assert json.loads(replay.stdout) == {"reason": "consumed", "result": "blocked"}
    combined = prepared.stdout + confirmed.stdout + consumed.stdout + replay.stdout
    assert OWNER not in combined
    assert ACCOUNT not in combined
    assert "signature" not in combined
    assert "nonce" not in combined


def test_arm_boundary_has_no_broker_or_http_client_imports() -> None:
    # Given
    root = Path(__file__).parents[1]
    files = (
        root / "trading_agent" / "hermes_arm_authority.py",
        root / "trading_agent" / "hermes_arm_gateway.py",
        root / "trading_agent" / "hermes_arm_request.py",
        root / "trading_agent" / "hermes_arm_signing.py",
        root / "trading_agent" / "hermes_arm_store.py",
    )

    # When
    imports = tuple(name for path in files for name in _imports(path))

    # Then
    assert not any(token in name for name in imports for token in ("alpaca", "broker", "httpx", "requests"))


def _fixture(tmp_path: Path) -> AuthorityFixture:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "fixture")
    commit_sha = _git(repository, "rev-parse", "HEAD").stdout.strip()
    state = tmp_path / "state"
    state.mkdir()
    lane_registry = state / "lane.sqlite3"
    experiment_ledger = state / "experiment.sqlite3"
    _lane_fixture(lane_registry)
    _experiment_fixture(experiment_ledger, commit_sha)
    signing_key = state / "hermes-arm.env"
    signing_key.write_text("HERMES_ARM_SIGNING_KEY=" + "k" * 64 + "\n", encoding="utf-8")
    signing_key.chmod(0o600)
    return AuthorityFixture(
        repository=repository,
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        signing_key=signing_key,
        arm_database=state / "arm.sqlite3",
        commit_sha=commit_sha,
    )


def _lane_fixture(path: Path) -> None:
    binding = lane_account_binding(INTRADAY_MANIFEST, ACCOUNT, "e" * 64, AT)
    with LaneRegistryStore(path).writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.bind_account(binding)


def _experiment_fixture(path: Path, commit_sha: str) -> None:
    hypothesis = _hypothesis()
    version = _version().model_copy(update={"code_version": commit_sha})
    binding = _strategy_authority_binding()
    registration = _lifecycle_registration().model_copy(
        update={
            "evidence_keys": tuple(
                sorted(
                    (
                        str(experiment_scope_key(ORB_SCOPE)),
                        str(hypothesis_registration_key(hypothesis)),
                        str(strategy_version_registration_key(version)),
                    )
                )
            )
        }
    )
    paper = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    challenger = _lifecycle_transition(
        paper,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(challenger, binding, StrategyLifecycleState.PAPER_CHAMPION)
    with ExperimentLedgerStore(path).writer() as writer:
        _ = writer.register_hypothesis(hypothesis)
        _ = writer.register_strategy_version(version)
        _ = writer.register_strategy_authority_binding(binding)
        _ = writer.append_lifecycle_event(registration)
        _ = writer.append_lifecycle_event(paper)
        _ = writer.append_lifecycle_event(challenger)
        _ = writer.append_lifecycle_event(champion)


def _run_cli(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        ("uv", "run", "python", str(root / "run_hermes_arm_gateway.py"), *arguments),
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return tuple(names)
