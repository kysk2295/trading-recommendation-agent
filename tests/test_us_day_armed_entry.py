from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from tests.us_day_operating_fixtures import admission
from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmRequest,
    HermesArmScope,
    HermesArmTransition,
    HermesArmTransitionKind,
)
from trading_agent.hermes_arm_signing import HermesArmSigner
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.lane_defaults import INTRADAY_PILOT_RISK_CONTRACT
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_entry_source import InvalidCurrentOrbPaperEntrySourceError
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_day_armed_entry import UsDayArmedEntryDependencies, main

NOW = dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC)
SESSION = "XNYS-2026-07-14"


class CapturingOperatingMain:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str]) -> int:
        self.calls.append(argv)
        return 0


def test_once_waits_for_setup_without_opening_arm_material(tmp_path: Path, capsys) -> None:
    accessed = False
    store_accessed = False

    def source(_: Path, __: dt.datetime):
        raise InvalidCurrentOrbPaperEntrySourceError

    def signer(_: Path) -> HermesArmSigner:
        nonlocal accessed
        accessed = True
        return HermesArmSigner.from_bytes(b"x" * 32)

    def store(_: Path, __: HermesArmSigner) -> HermesArmStore:
        nonlocal store_accessed
        store_accessed = True
        raise AssertionError

    dependencies = UsDayArmedEntryDependencies(
        clock=lambda: NOW,
        source_loader=source,
        signer_loader=signer,
        operating_main=CapturingOperatingMain(),
        store_factory=store,
        sleeper=lambda _: None,
    )
    exit_code = main(_args(tmp_path, "--once"), dependencies)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"result": "waiting_setup"}
    assert not accessed
    assert not store_accessed


def test_once_waits_when_setup_has_no_owner_arm(tmp_path: Path, capsys) -> None:
    store = HermesArmStore(tmp_path / "arm.sqlite3", HermesArmSigner.from_bytes(b"x" * 32))
    store._initialize()

    exit_code = main(
        _args(tmp_path, "--once"), _deps(_current_source, lambda _: store._signer, CapturingOperatingMain(), store)
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"result": "waiting_owner_arm"}


def test_confirmed_matching_arm_dispatches_operating_run_once(tmp_path: Path, capsys) -> None:
    signer = HermesArmSigner.from_bytes(b"x" * 32)
    store = HermesArmStore(tmp_path / "arm.sqlite3", signer)
    request = _request(signer, "a" * 64, HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM))
    store.add_request(request)
    _transition(store, signer, request, HermesArmTransitionKind.CONFIRMED)
    operating = CapturingOperatingMain()

    exit_code = main(_args(tmp_path, "--once"), _deps(_current_source, lambda _: signer, operating, store))

    assert exit_code == 0
    assert len(operating.calls) == 1
    assert operating.calls[0][0] == "run"
    assert request.request_id in operating.calls[0]
    assert request.request_id not in capsys.readouterr().out


def test_ambiguous_confirmed_arms_block_without_dispatch(tmp_path: Path, capsys) -> None:
    signer = HermesArmSigner.from_bytes(b"x" * 32)
    store = HermesArmStore(tmp_path / "arm.sqlite3", signer)
    for request_id in ("a" * 64, "b" * 64):
        request = _request(signer, request_id, HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM))
        store.add_request(request)
        _transition(store, signer, request, HermesArmTransitionKind.CONFIRMED)
    operating = CapturingOperatingMain()

    exit_code = main(_args(tmp_path, "--once"), _deps(_current_source, lambda _: signer, operating, store))

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"result": "blocked_ambiguous_confirmed_arm"}
    assert not operating.calls


def test_excluded_arms_leave_owner_waiting(tmp_path: Path, capsys) -> None:
    signer = HermesArmSigner.from_bytes(b"x" * 32)
    store = HermesArmStore(tmp_path / "arm.sqlite3", signer)
    expired = _request(
        signer,
        "a" * 64,
        HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM),
        NOW - dt.timedelta(seconds=1),
    )
    revoked = _request(signer, "b" * 64, HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM))
    wrong = _request(signer, "c" * 64, HermesArmScope(session_id="XNYS-2026-07-15", lane_id=LaneId.INTRADAY_MOMENTUM))
    for request in (expired, revoked, wrong):
        store.add_request(request)
    _transition(store, signer, revoked, HermesArmTransitionKind.CONFIRMED)
    _transition(store, signer, revoked, HermesArmTransitionKind.REVOKED)

    exit_code = main(
        _args(tmp_path, "--once"), _deps(_current_source, lambda _: signer, CapturingOperatingMain(), store)
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"result": "waiting_owner_arm"}


def test_cutoff_censors_before_source_access(tmp_path: Path, capsys) -> None:
    accessed = False

    def source(_: Path, __: dt.datetime):
        nonlocal accessed
        accessed = True
        return admission()

    exit_code = main(
        _args(tmp_path, "--once", "--entry-cutoff", "2026-07-14T09:35:00-04:00"),
        _deps(source, lambda _: HermesArmSigner.from_bytes(b"x" * 32), CapturingOperatingMain()),
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"result": "censored"}
    assert not accessed


def test_malformed_relative_path_blocks_before_source_access(tmp_path: Path, capsys) -> None:
    accessed = False

    def source(_: Path, __: dt.datetime):
        nonlocal accessed
        accessed = True
        return admission()

    exit_code = main(
        ("--arm-database", "relative.sqlite3"),
        _deps(source, lambda _: HermesArmSigner.from_bytes(b"x" * 32), CapturingOperatingMain()),
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"reason": "invalid_command", "result": "blocked"}
    assert not accessed


def _current_source(_: Path, __: dt.datetime):
    return admission()


def _deps(
    source: Callable[[Path, dt.datetime], PaperOrderAdmissionRequest],
    signer: Callable[[Path], HermesArmSigner],
    operating: CapturingOperatingMain,
    store: HermesArmStore | None = None,
) -> UsDayArmedEntryDependencies:
    return UsDayArmedEntryDependencies(
        clock=lambda: NOW,
        source_loader=source,
        signer_loader=signer,
        operating_main=operating,
        store_factory=lambda _, supplied: store or HermesArmStore(Path("/unused"), supplied),
        sleeper=lambda _: None,
    )


def _args(tmp_path: Path, *extra: str) -> tuple[str, ...]:
    return (
        "--arm-database",
        str(tmp_path / "arm.sqlite3"),
        "--delivery-database",
        str(tmp_path / "delivery.sqlite3"),
        "--execution-database",
        str(tmp_path / "execution.sqlite3"),
        "--watch-database",
        str(tmp_path / "watch.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--repository",
        str(tmp_path),
        "--signing-key",
        str(tmp_path / "signing.env"),
        "--session-id",
        SESSION,
        "--entry-cutoff",
        "2026-07-14T12:00:00-04:00",
        "--poll-interval-seconds",
        "1",
        *extra,
    )


def _request(
    signer: HermesArmSigner, request_id: str, scope: HermesArmScope, expires_at: dt.datetime | None = None
) -> HermesArmRequest:
    unsigned = HermesArmRequest(
        request_id=request_id,
        owner_id_hash="d" * 64,
        authority=HermesArmAuthority(
            scope=scope,
            strategy_version="orb-v1",
            account_fingerprint="e" * 64,
            risk_contract_hash=HermesArmAuthority.risk_hash(INTRADAY_PILOT_RISK_CONTRACT),
            commit_sha="f" * 40,
            champion_binding_key="0" * 64,
        ),
        nonce_hash="1" * 64,
        confirmation_hash="2" * 64,
        prepared_at=NOW - dt.timedelta(minutes=1),
        expires_at=expires_at or NOW + dt.timedelta(minutes=1),
        signature="3" * 64,
    )
    return unsigned.model_copy(update={"signature": signer.sign(_payload(unsigned))})


def _transition(
    store: HermesArmStore, signer: HermesArmSigner, request: HermesArmRequest, kind: HermesArmTransitionKind
) -> None:
    previous = store.transitions(request.request_id)
    unsigned = HermesArmTransition(
        request_id=request.request_id,
        sequence=len(previous) + 1,
        kind=kind,
        occurred_at=NOW,
        previous_signature=None if not previous else previous[-1].signature,
        signature="0" * 64,
    )
    transition = unsigned.model_copy(update={"signature": signer.sign(_payload(unsigned))})
    allowed = (None,) if kind is HermesArmTransitionKind.CONFIRMED else (HermesArmTransitionKind.CONFIRMED,)
    store.append_transition(transition, allowed)


def _payload(value: HermesArmRequest | HermesArmTransition) -> str:
    return json.dumps(value.model_dump(mode="json", exclude={"signature"}), separators=(",", ":"), sort_keys=True)
