from __future__ import annotations

import datetime as dt
import json
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.challenger_replay_fixtures import write_closed_source_session
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_directed_jobs import (
    DirectedJobExecutor,
    DirectedJobRequest,
)
from trading_agent.dashboard_directed_research_models import (
    DirectedResearchKind,
    DirectedResearchReceipt,
)
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader

INTERACTION_ID = "019c0014-f0f5-7000-8000-000000000010"
PROJECT = Path(__file__).resolve().parents[1]
HYPOTHESIS_MANIFEST = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"
ENTITLEMENT = PROJECT / "examples" / "data" / "kis-us-candidate-minute-historical-research-v1.json"
NOW = dt.datetime(2026, 7, 23, 5, 30, tzinfo=dt.UTC)


class _RecordingBroker:
    def __init__(
        self,
        raw: bytes,
        error: OSError | TimeoutError | None = None,
        effect: Callable[[], None] | None = None,
    ) -> None:
        self.raw = raw
        self.error = error
        self.effect = effect
        self.calls = 0

    def execute(self, operation: DirectedResearchKind, family_id: AgentFamilyId) -> bytes:
        del operation, family_id
        self.calls += 1
        if self.effect is not None:
            self.effect()
        if self.error is not None:
            raise self.error
        return self.raw


def test_hypothesis_job_mutates_authoritative_experiment_ledger(tmp_path: Path) -> None:
    # Given: an owner-controlled typed hypothesis package at the fixed broker boundary
    source = _write_hypothesis_package(tmp_path)
    executor = _executor(tmp_path, source)
    request = _request("hypothesis")

    # When: the directed hypothesis operation completes
    events = executor.execute(request)

    # Then: its terminal result is backed by the real typed hypothesis/card ledger
    reader = _reader(tmp_path)
    assert len(reader.hypotheses()) == 1
    assert len(reader.research_hypothesis_cards()) == 1
    assert events[-1].result_sha256 == str(reader.research_hypothesis_cards()[0].card_key)


@pytest.mark.parametrize("operation", ["research", "analysis"])
def test_query_job_publishes_real_authoritative_queue_output(
    tmp_path: Path,
    operation: DirectedResearchKind,
) -> None:
    # Given: a typed source-bound hypothesis package and an empty real ledger
    source = _write_hypothesis_package(tmp_path)
    executor = _executor(tmp_path, source)

    # When: research or analysis executes through the code-owned projector
    events = executor.execute(_request(operation))

    # Then: the output is the real projected queue snapshot backed by ledger rows
    reader = _reader(tmp_path)
    result_sha = events[-1].result_sha256
    assert len(reader.research_hypothesis_cards()) == 1
    assert result_sha is not None
    assert (
        tmp_path
        / "state"
        / "authoritative"
        / "systematic_quant"
        / operation
        / f"source_hypothesis_queue_{result_sha}.json"
    ).is_file()


def test_experiment_job_runs_actual_bounded_trial_and_terminal_receipt(tmp_path: Path) -> None:
    # Given: fixed local session, entitlement, hypothesis, and bounded experiment spec
    source = _write_experiment_package(tmp_path)
    executor = _executor(tmp_path, source)

    # When: the directed experiment invokes the real local research loop
    events = executor.execute(_request("experiment"))

    # Then: the authoritative ledger and artifacts contain a completed trial and review
    reader = _reader(tmp_path)
    trial = reader.trials()[0].registration
    terminal = reader.trial_events(trial.trial_id)[-1].event
    family_root = tmp_path / "state" / "authoritative" / "systematic_quant"
    assert terminal.event_kind is TrialEventKind.COMPLETED
    assert events[-1].result_sha256 == terminal.artifact_sha256s[0]
    assert tuple((family_root / "artifacts").glob("intraday_walk_forward_*.json"))
    assert tuple((family_root / "reviews").glob("intraday_research_review_*.json"))


@pytest.mark.parametrize(
    "raw",
    [
        b"completed",
        b'{"operation":"hypothesis","terminal":"completed"}',
        (
            b'{"operation":"hypothesis","terminal":"completed","domain_effects":0,'
            b'"evidence_sha256s":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],'
            b'"result_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            b'"summary":"no-op"}'
        ),
    ],
)
def test_broker_text_only_malformed_and_noop_receipts_fail_closed(
    tmp_path: Path,
    raw: bytes,
) -> None:
    # Given: a broker that returns no authoritative completed domain effect
    broker = _RecordingBroker(raw)
    executor = _executor(tmp_path, tmp_path, broker)

    # When / Then: strict parsing closes the append-only chain as uncertain
    events = executor.execute(_request("hypothesis"))
    persisted = (tmp_path / "state" / INTERACTION_ID / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert broker.calls == 1
    assert events[-1].kind == "result"
    assert events[-1].state == "uncertain"
    assert json.loads(persisted[-1])["state"] == "uncertain"


@pytest.mark.parametrize("error", [OSError("broker crash"), TimeoutError("broker timeout")])
def test_broker_crash_and_timeout_launch_once_and_fail_closed(
    tmp_path: Path,
    error: OSError | TimeoutError,
) -> None:
    # Given: a code-owned broker that crashes or exhausts its bounded runtime
    broker = _RecordingBroker(b"", error)
    executor = _executor(tmp_path, tmp_path, broker)

    # When / Then: one call closes uncertain and replay cannot invoke the broker again
    events = executor.execute(_request("experiment"))
    with pytest.raises(FileExistsError):
        _ = executor.execute(_request("experiment"))
    assert broker.calls == 1
    assert events[-1].state == "uncertain"


def test_crash_after_real_ledger_write_closes_uncertain_without_retry(tmp_path: Path) -> None:
    # Given: an injected broker mutates the real ledger and crashes before returning a receipt
    source = _write_hypothesis_package(tmp_path)
    real = _executor(tmp_path, source)._research_broker

    def register_effect() -> None:
        _ = real.execute("hypothesis", "systematic_quant")

    broker = _RecordingBroker(
        b"",
        OSError("receipt lost"),
        effect=register_effect,
    )
    executor = _executor(tmp_path, source, broker)

    # When: the post-effect receipt seam crashes
    events = executor.execute(_request("hypothesis"))

    # Then: the real effect remains, terminal authority is uncertain, and replay is blocked
    assert len(_reader(tmp_path).research_hypothesis_cards()) == 1
    assert events[-1].state == "uncertain"
    with pytest.raises(FileExistsError):
        _ = executor.execute(_request("hypothesis"))
    assert broker.calls == 1


def test_allowed_code_runs_fixed_git_boundary_without_research_broker(tmp_path: Path) -> None:
    # Given: a fixed allowlisted code check and a broker that must remain unused
    broker = _RecordingBroker(b"")
    executor = _executor(tmp_path, tmp_path, broker)

    # When: the code check executes
    events = executor.execute(_request("allowed_code"))

    # Then: its immutable receipt is terminal and research/provider paths stay closed
    assert events[-1].state == "completed"
    assert (tmp_path / "state" / INTERACTION_ID / "code-check-receipt.json").is_file()
    assert broker.calls == 0


def test_directed_job_rejects_forbidden_tool_before_broker_execution() -> None:
    # Given: a request attempting provider or Paper mutation
    request = {
        "interaction_id": INTERACTION_ID,
        "agent_family_id": "day_trading",
        "job_kind": "paper_order",
        "command": "live order",
    }

    # When / Then: the typed allowlist rejects it before any operation
    with pytest.raises(ValidationError):
        DirectedJobRequest.model_validate(request)


def _executor(
    tmp_path: Path,
    source: Path,
    broker: _RecordingBroker | None = None,
) -> DirectedJobExecutor:
    return DirectedJobExecutor(
        state_root=tmp_path / "state",
        source_evidence_root=source,
        repository=PROJECT,
        research_broker=broker,
    )


def _request(kind: str) -> DirectedJobRequest:
    return DirectedJobRequest.model_validate(
        {
            "interaction_id": INTERACTION_ID,
            "agent_family_id": "systematic_quant",
            "job_kind": kind,
            "command": "권위 있는 증거 경계에서 실제 작업을 수행해줘",
        }
    )


def _reader(tmp_path: Path) -> ExperimentLedgerReader:
    return ExperimentLedgerReader(tmp_path / "state" / "authoritative" / "systematic_quant" / "experiment.sqlite3")


def _write_hypothesis_package(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    package = source / "directed-research"
    package.mkdir(parents=True)
    source.chmod(0o700)
    package.chmod(0o700)
    (package / "hypothesis.json").write_bytes(HYPOTHESIS_MANIFEST.read_bytes())
    (package / "hypothesis.json").chmod(0o600)
    return source


def _write_experiment_package(tmp_path: Path) -> Path:
    source = _write_hypothesis_package(tmp_path)
    package = source / "directed-research"
    session_date = dt.date(2026, 7, 14)
    write_closed_source_session(
        package / "sessions" / session_date.isoformat(),
        session_date=session_date,
    )
    _make_tree_private(package / "sessions")
    (package / "entitlement.json").write_bytes(ENTITLEMENT.read_bytes())
    (package / "entitlement.json").chmod(0o600)
    (package / "experiment.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_dates": [session_date.isoformat()],
                "required_session_dates": [session_date.isoformat()],
                "strategy": "vwap_reclaim",
                "strategy_version": "directed_vwap_reclaim_20260714_v1",
                "dataset_producer_commit_sha": "d" * 40,
                "code_version": "e" * 40,
                "registered_at": NOW.isoformat(),
                "observed_at": NOW.isoformat(),
                "minimum_clean_sessions": 1,
                "minimum_training_sessions": 0,
                "max_sessions": 2,
                "max_bars": 500,
                "per_side_fee_bps": 5,
                "per_side_slippage_bps": 15,
                "bootstrap_samples": 200,
                "rss_limit_gib": 9.5,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (package / "experiment.json").chmod(0o600)
    return source


@pytest.mark.parametrize("mutation", ["public", "symlink", "hardlink"])
def test_unsafe_hypothesis_package_input_mutates_no_ledger(
    tmp_path: Path,
    mutation: str,
) -> None:
    # Given: the fixed hypothesis input is public, a symlink, or a hardlink alias
    source = _write_hypothesis_package(tmp_path)
    manifest = source / "directed-research" / "hypothesis.json"
    match mutation:
        case "public":
            manifest.chmod(0o644)
        case "symlink":
            external = tmp_path / "external.json"
            external.write_bytes(manifest.read_bytes())
            external.chmod(0o600)
            manifest.unlink()
            manifest.symlink_to(external)
        case "hardlink":
            alias = tmp_path / "manifest-alias.json"
            alias.hardlink_to(manifest)
        case unexpected:
            raise AssertionError(unexpected)

    # When: hypothesis registration crosses the broker boundary
    events = _executor(tmp_path, source).execute(_request("hypothesis"))

    # Then: the unsafe input closes uncertain before any authoritative ledger mutation
    ledger = tmp_path / "state" / "authoritative" / "systematic_quant" / "experiment.sqlite3"
    assert events[-1].state == "uncertain"
    assert not ledger.exists()


@pytest.mark.parametrize(
    "mutation",
    ["public_spec", "symlink_entitlement", "public_session_dir", "hardlink_session_file"],
)
def test_unsafe_experiment_package_input_mutates_no_ledger(
    tmp_path: Path,
    mutation: str,
) -> None:
    # Given: one experiment package component violates fixed private identity
    source = _write_experiment_package(tmp_path)
    package = source / "directed-research"
    match mutation:
        case "public_spec":
            (package / "experiment.json").chmod(0o644)
        case "symlink_entitlement":
            entitlement = package / "entitlement.json"
            external = tmp_path / "entitlement.json"
            external.write_bytes(entitlement.read_bytes())
            external.chmod(0o600)
            entitlement.unlink()
            entitlement.symlink_to(external)
        case "public_session_dir":
            next((package / "sessions").iterdir()).chmod(0o755)
        case "hardlink_session_file":
            session_file = next(path for path in (package / "sessions").rglob("*") if path.is_file())
            (tmp_path / "session-alias").hardlink_to(session_file)
        case unexpected:
            raise AssertionError(unexpected)

    # When: the bounded experiment crosses the broker boundary
    events = _executor(tmp_path, source).execute(_request("experiment"))

    # Then: validation fails before hypothesis, queue, lane, or trial mutation
    ledger = tmp_path / "state" / "authoritative" / "systematic_quant" / "experiment.sqlite3"
    assert events[-1].state == "uncertain"
    assert not ledger.exists()


@pytest.mark.parametrize("mutation", ["public", "symlink", "hardlink"])
def test_unsafe_authoritative_family_root_causes_zero_external_write(
    tmp_path: Path,
    mutation: str,
) -> None:
    # Given: the authoritative family output root is public or aliases external state
    source = _write_hypothesis_package(tmp_path)
    state = tmp_path / "state"
    authority = state / "authoritative"
    authority.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    authority.chmod(0o700)
    family = authority / "systematic_quant"
    external = tmp_path / "external-output"
    match mutation:
        case "public":
            family.mkdir(mode=0o755)
        case "symlink":
            external.mkdir(mode=0o700)
            family.symlink_to(external, target_is_directory=True)
        case "hardlink":
            external.write_bytes(b"external")
            external.chmod(0o600)
            family.hardlink_to(external)
        case unexpected:
            raise AssertionError(unexpected)

    # When: a hypothesis job resolves its code-owned output root
    events = _executor(tmp_path, source).execute(_request("hypothesis"))

    # Then: it closes uncertain without changing permissions or writing outside authority
    assert events[-1].state == "uncertain"
    if mutation == "public":
        assert stat.S_IMODE(family.stat().st_mode) == 0o755
    else:
        assert not external.is_dir() or tuple(external.iterdir()) == ()
        assert not (external / "experiment.sqlite3").exists()


def _make_tree_private(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _valid_receipt(operation: DirectedResearchKind) -> bytes:
    return (
        DirectedResearchReceipt(
            operation=operation,
            terminal="completed",
            domain_effects=1,
            evidence_sha256s=("a" * 64,),
            result_sha256="b" * 64,
            summary="authoritative broker completed",
        )
        .model_dump_json()
        .encode()
    )
