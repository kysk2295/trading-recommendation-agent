from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmConfirmCommand,
    HermesArmConsumeCommand,
    HermesArmFailure,
    HermesArmPrepareCommand,
    HermesArmRequest,
    HermesArmRequestStatus,
    HermesArmRevokeCommand,
    HermesArmScope,
    HermesArmTransition,
    HermesArmTransitionKind,
    InvalidHermesArmRequestError,
    PreparedHermesArm,
)
from trading_agent.hermes_arm_signing import HermesArmSigner
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm


class HermesArmAuthorityResolver(Protocol):
    def resolve(self, scope: HermesArmScope) -> HermesArmAuthority: ...


@dataclass(frozen=True, slots=True)
class HermesArmGatewayConfig:
    store: HermesArmStore
    authority_resolver: HermesArmAuthorityResolver
    signer: HermesArmSigner
    clock: Callable[[], dt.datetime]
    nonce_factory: Callable[[], bytes]
    ttl_seconds: int


class HermesArmGateway:
    __slots__ = ("_config",)

    def __init__(self, config: HermesArmGatewayConfig) -> None:
        self._config = config

    def prepare(self, command: HermesArmPrepareCommand) -> PreparedHermesArm:
        _require_hash(command.owner_id_hash)
        authority = self._config.authority_resolver.resolve(command.scope)
        now = self._now()
        expires_at = now + dt.timedelta(seconds=self._config.ttl_seconds)
        nonce = self._config.nonce_factory()
        material = _canonical({"authority": authority.model_dump(mode="json"), "prepared_at": now.isoformat()})
        request_id = hashlib.sha256(material.encode() + nonce).hexdigest()
        confirmation = self._config.signer.confirmation(nonce, request_id)
        unsigned = HermesArmRequest(
            request_id=request_id,
            owner_id_hash=command.owner_id_hash,
            authority=authority,
            nonce_hash=hashlib.sha256(nonce).hexdigest(),
            confirmation_hash=hashlib.sha256(confirmation.encode()).hexdigest(),
            prepared_at=now,
            expires_at=expires_at,
            signature="0" * 64,
        )
        signature = self._config.signer.sign(_request_payload(unsigned))
        request = unsigned.model_copy(update={"signature": signature})
        self._config.store.add_request(request)
        return PreparedHermesArm(request_id=request_id, confirmation=confirmation, expires_at=expires_at)

    def confirm(self, command: HermesArmConfirmCommand) -> HermesArmRequestStatus:
        request = self._config.store.request(command.request_id)
        _require_owner(request, command.owner_id_hash)
        self._expire_if_needed(request)
        supplied_hash = hashlib.sha256(command.confirmation.encode()).hexdigest()
        if not hmac.compare_digest(request.confirmation_hash, supplied_hash):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_CONFIRMATION)
        self._append(request, HermesArmTransitionKind.CONFIRMED, (None,))
        return self.status(request.request_id)

    def consume(self, command: HermesArmConsumeCommand) -> PaperMutationArm:
        request = self._config.store.request(command.request_id)
        _require_scope(request.authority.scope, command.expected_scope)
        self._expire_if_needed(request)
        current = self._current_status(request)
        if current is not HermesArmTransitionKind.CONFIRMED:
            raise InvalidHermesArmRequestError(_status_failure(current))
        authority = self._config.authority_resolver.resolve(command.expected_scope)
        _require_authority(request.authority, authority)
        self._append(request, HermesArmTransitionKind.CONSUMED, (HermesArmTransitionKind.CONFIRMED,))
        return PaperMutationArm(PAPER_MUTATION_ARM_VALUE)

    def revoke(self, command: HermesArmRevokeCommand) -> HermesArmRequestStatus:
        request = self._config.store.request(command.request_id)
        _require_owner(request, command.owner_id_hash)
        self._expire_if_needed(request)
        current = self._current_status(request)
        if current in {HermesArmTransitionKind.CONSUMED, HermesArmTransitionKind.REVOKED}:
            raise InvalidHermesArmRequestError(_status_failure(current))
        self._append(request, HermesArmTransitionKind.REVOKED, (None, HermesArmTransitionKind.CONFIRMED))
        return self.status(request.request_id)

    def status(self, request_id: str) -> HermesArmRequestStatus:
        request = self._config.store.request(request_id)
        self._expire_if_needed(request)
        return HermesArmRequestStatus(
            request_id=request_id,
            status=self._current_status(request),
            expires_at=request.expires_at,
        )

    def _expire_if_needed(self, request: HermesArmRequest) -> None:
        current = self._current_status(request)
        if self._now() <= request.expires_at or current in {
            HermesArmTransitionKind.CONSUMED,
            HermesArmTransitionKind.REVOKED,
            HermesArmTransitionKind.EXPIRED,
        }:
            return
        self._append(request, HermesArmTransitionKind.EXPIRED, (None, HermesArmTransitionKind.CONFIRMED))
        raise InvalidHermesArmRequestError(HermesArmFailure.EXPIRED)

    def _append(
        self,
        request: HermesArmRequest,
        kind: HermesArmTransitionKind,
        allowed_previous: tuple[HermesArmTransitionKind | None, ...],
    ) -> None:
        transitions = self._config.store.transitions(request.request_id)
        previous = None if not transitions else transitions[-1].signature
        unsigned = HermesArmTransition(
            request_id=request.request_id,
            sequence=len(transitions) + 1,
            kind=kind,
            occurred_at=self._now(),
            previous_signature=previous,
            signature="0" * 64,
        )
        transition = unsigned.model_copy(update={"signature": self._config.signer.sign(_transition_payload(unsigned))})
        self._config.store.append_transition(transition, allowed_previous)

    def _current_status(self, request: HermesArmRequest) -> HermesArmTransitionKind | None:
        transitions = self._config.store.transitions(request.request_id)
        return None if not transitions else transitions[-1].kind

    def _now(self) -> dt.datetime:
        value = self._config.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
        return value


def _require_hash(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)


def _require_owner(request: HermesArmRequest, owner_id_hash: str) -> None:
    _require_hash(owner_id_hash)
    if not hmac.compare_digest(request.owner_id_hash, owner_id_hash):
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_OWNER)


def _require_scope(actual: HermesArmScope, expected: HermesArmScope) -> None:
    if actual.session_id != expected.session_id:
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION)
    if actual.lane_id is not expected.lane_id:
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_LANE)


def _require_authority(expected: HermesArmAuthority, actual: HermesArmAuthority) -> None:
    if expected.risk_contract_hash != actual.risk_contract_hash:
        raise InvalidHermesArmRequestError(HermesArmFailure.RISK_MISMATCH)
    if expected.account_fingerprint != actual.account_fingerprint:
        raise InvalidHermesArmRequestError(HermesArmFailure.ACCOUNT_MISMATCH)
    if expected.commit_sha != actual.commit_sha:
        raise InvalidHermesArmRequestError(HermesArmFailure.COMMIT_MISMATCH)
    if actual.champion_binding_key is None:
        raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISSING)
    if expected.champion_binding_key != actual.champion_binding_key:
        raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISMATCH)
    if expected.strategy_version != actual.strategy_version:
        raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISMATCH)


def _status_failure(status: HermesArmTransitionKind | None) -> HermesArmFailure:
    if status is HermesArmTransitionKind.CONSUMED:
        return HermesArmFailure.CONSUMED
    if status is HermesArmTransitionKind.REVOKED:
        return HermesArmFailure.REVOKED
    if status is HermesArmTransitionKind.EXPIRED:
        return HermesArmFailure.EXPIRED
    if status is HermesArmTransitionKind.CONFIRMED:
        return HermesArmFailure.CONFIRMATION_REPLAYED
    return HermesArmFailure.NOT_CONFIRMED


def _canonical(values: dict[str, str | dict[str, str]]) -> str:
    return json.dumps(values, separators=(",", ":"), sort_keys=True)


def _request_payload(request: HermesArmRequest) -> str:
    return json.dumps(request.model_dump(mode="json", exclude={"signature"}), separators=(",", ":"), sort_keys=True)


def _transition_payload(transition: HermesArmTransition) -> str:
    return json.dumps(
        transition.model_dump(mode="json", exclude={"signature"}), separators=(",", ":"), sort_keys=True
    )
