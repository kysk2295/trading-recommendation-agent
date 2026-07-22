from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from trading_agent.hermes_arm_gateway import (
    HermesArmGateway,
    HermesArmGatewayConfig,
)
from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmConfirmCommand,
    HermesArmConsumeCommand,
    HermesArmFailure,
    HermesArmPrepareCommand,
    HermesArmRevokeCommand,
    HermesArmScope,
    HermesArmTransitionKind,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_signing import HermesArmSigner, load_hermes_arm_signing_key
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.lane_defaults import INTRADAY_PILOT_RISK_CONTRACT
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm

AT = dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC)
OWNER = "a" * 64
OTHER_OWNER = "b" * 64
ACCOUNT = "c" * 64
COMMIT = "d" * 40
STRATEGY = "orb-v1"
SCOPE = HermesArmScope(session_id="XNYS-2026-07-22", lane_id=LaneId.INTRADAY_MOMENTUM)


class ManualClock:
    __slots__ = ("current",)

    def __init__(self) -> None:
        self.current = AT

    def __call__(self) -> dt.datetime:
        return self.current


class MutableAuthorityResolver:
    __slots__ = ("authority", "failure")

    def __init__(self) -> None:
        self.authority = _authority()
        self.failure: HermesArmFailure | None = None

    def resolve(self, scope: HermesArmScope) -> HermesArmAuthority:
        if self.failure is not None:
            raise InvalidHermesArmRequestError(self.failure)
        if scope != self.authority.scope:
            raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISSING)
        return self.authority


class DeterministicNonce:
    __slots__ = ("_counter",)

    def __init__(self) -> None:
        self._counter = 0

    def __call__(self) -> bytes:
        self._counter += 1
        return self._counter.to_bytes(32)


def test_confirmed_arm_is_consumed_once_for_exact_session_lane_and_risk(tmp_path: Path) -> None:
    # Given
    gateway, _, _ = _gateway(tmp_path)
    prepared = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    confirmed = gateway.confirm(
        HermesArmConfirmCommand(
            owner_id_hash=OWNER,
            request_id=prepared.request_id,
            confirmation=prepared.confirmation,
        )
    )

    # When
    arm = gateway.consume(HermesArmConsumeCommand(request_id=confirmed.request_id, expected_scope=SCOPE))

    # Then
    assert arm == PaperMutationArm(PAPER_MUTATION_ARM_VALUE)
    with pytest.raises(InvalidHermesArmRequestError) as replay:
        _ = gateway.consume(HermesArmConsumeCommand(request_id=confirmed.request_id, expected_scope=SCOPE))
    assert replay.value.reason is HermesArmFailure.CONSUMED


def test_wrong_owner_and_replayed_confirmation_are_rejected(tmp_path: Path) -> None:
    # Given
    gateway, _, _ = _gateway(tmp_path)
    prepared = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as wrong_owner:
        _ = gateway.confirm(
            HermesArmConfirmCommand(
                owner_id_hash=OTHER_OWNER,
                request_id=prepared.request_id,
                confirmation=prepared.confirmation,
            )
        )
    assert wrong_owner.value.reason is HermesArmFailure.WRONG_OWNER
    command = HermesArmConfirmCommand(
        owner_id_hash=OWNER,
        request_id=prepared.request_id,
        confirmation=prepared.confirmation,
    )
    _ = gateway.confirm(command)
    with pytest.raises(InvalidHermesArmRequestError) as replay:
        _ = gateway.confirm(command)
    assert replay.value.reason is HermesArmFailure.CONFIRMATION_REPLAYED


@pytest.mark.parametrize(
    ("scope", "reason"),
    (
        (
            HermesArmScope(session_id="XNYS-2026-07-23", lane_id=LaneId.INTRADAY_MOMENTUM),
            HermesArmFailure.WRONG_SESSION,
        ),
        (
            HermesArmScope(session_id="XNYS-2026-07-22", lane_id=LaneId.SWING_MOMENTUM),
            HermesArmFailure.WRONG_LANE,
        ),
    ),
)
def test_consume_rejects_wrong_scope(tmp_path: Path, scope: HermesArmScope, reason: HermesArmFailure) -> None:
    # Given
    gateway, _, _ = _gateway(tmp_path)
    confirmed = _confirmed(gateway)

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        _ = gateway.consume(HermesArmConsumeCommand(request_id=confirmed.request_id, expected_scope=scope))
    assert blocked.value.reason is reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("risk_contract_hash", "e" * 64, HermesArmFailure.RISK_MISMATCH),
        ("account_fingerprint", "f" * 64, HermesArmFailure.ACCOUNT_MISMATCH),
        ("commit_sha", "1" * 40, HermesArmFailure.COMMIT_MISMATCH),
        ("champion_binding_key", None, HermesArmFailure.CHAMPION_MISSING),
    ),
)
def test_consume_revalidates_all_authority_bindings(
    tmp_path: Path,
    field: str,
    value: str | None,
    reason: HermesArmFailure,
) -> None:
    # Given
    gateway, resolver, _ = _gateway(tmp_path)
    confirmed = _confirmed(gateway)
    resolver.authority = resolver.authority.model_copy(update={field: value})

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        _ = gateway.consume(HermesArmConsumeCommand(request_id=confirmed.request_id, expected_scope=SCOPE))
    assert blocked.value.reason is reason


def test_expired_and_revoked_requests_cannot_be_confirmed_or_consumed(tmp_path: Path) -> None:
    # Given
    gateway, _, clock = _gateway(tmp_path)
    expired = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    clock.current = AT + dt.timedelta(minutes=6)

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as expiry:
        _ = gateway.confirm(
            HermesArmConfirmCommand(
                owner_id_hash=OWNER,
                request_id=expired.request_id,
                confirmation=expired.confirmation,
            )
        )
    assert expiry.value.reason is HermesArmFailure.EXPIRED
    assert gateway.status(expired.request_id).status is HermesArmTransitionKind.EXPIRED

    clock.current = AT
    revoked = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    _ = gateway.revoke(HermesArmRevokeCommand(owner_id_hash=OWNER, request_id=revoked.request_id))
    with pytest.raises(InvalidHermesArmRequestError) as revocation:
        _ = gateway.consume(HermesArmConsumeCommand(request_id=revoked.request_id, expected_scope=SCOPE))
    assert revocation.value.reason is HermesArmFailure.REVOKED


def test_dirty_repository_authority_blocks_prepare(tmp_path: Path) -> None:
    # Given
    gateway, resolver, _ = _gateway(tmp_path)
    resolver.failure = HermesArmFailure.DIRTY_COMMIT

    # When / Then
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        _ = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    assert blocked.value.reason is HermesArmFailure.DIRTY_COMMIT


def test_signing_key_loader_requires_owner_mode_600_regular_file(tmp_path: Path) -> None:
    # Given
    key_file = tmp_path / "hermes-arm.env"
    key_file.write_text("HERMES_ARM_SIGNING_KEY=" + "k" * 64 + "\n", encoding="utf-8")
    key_file.chmod(0o600)

    # When
    loaded = load_hermes_arm_signing_key(key_file)

    # Then
    assert repr(loaded) == "HermesArmSigningKey()"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    key_file.chmod(0o644)
    with pytest.raises(InvalidHermesArmRequestError) as insecure:
        _ = load_hermes_arm_signing_key(key_file)
    assert insecure.value.reason is HermesArmFailure.INVALID_SIGNING_KEY
    key_file.chmod(0o600)
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(key_file)
    with pytest.raises(InvalidHermesArmRequestError) as linked:
        _ = load_hermes_arm_signing_key(symlink)
    assert linked.value.reason is HermesArmFailure.INVALID_SIGNING_KEY


def _gateway(tmp_path: Path) -> tuple[HermesArmGateway, MutableAuthorityResolver, ManualClock]:
    signer = HermesArmSigner.from_bytes(b"x" * 32)
    resolver = MutableAuthorityResolver()
    clock = ManualClock()
    store = HermesArmStore(tmp_path / "arm.sqlite3", signer=signer)
    gateway = HermesArmGateway(
        HermesArmGatewayConfig(
            store=store,
            authority_resolver=resolver,
            signer=signer,
            clock=clock,
            nonce_factory=DeterministicNonce(),
            ttl_seconds=300,
        )
    )
    return gateway, resolver, clock


def _confirmed(gateway: HermesArmGateway):
    prepared = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    return gateway.confirm(
        HermesArmConfirmCommand(
            owner_id_hash=OWNER,
            request_id=prepared.request_id,
            confirmation=prepared.confirmation,
        )
    )


def _authority() -> HermesArmAuthority:
    return HermesArmAuthority(
        scope=SCOPE,
        strategy_version=STRATEGY,
        account_fingerprint=ACCOUNT,
        risk_contract_hash=HermesArmAuthority.risk_hash(INTRADAY_PILOT_RISK_CONTRACT),
        commit_sha=COMMIT,
        champion_binding_key="f" * 64,
    )
