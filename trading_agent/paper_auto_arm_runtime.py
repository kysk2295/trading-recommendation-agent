from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass

from trading_agent.hermes_arm_request import (
    HermesArmAuthority,
    HermesArmConsumeCommand,
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_auto_arm_policy import (
    PaperAutoArmPolicy,
    canonical_paper_auto_arm_policy_json,
    verify_paper_auto_arm_policy,
)
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class SessionScopedPaperAutoArmConsumer:
    __slots__ = ("_consumed", "_request_id", "_scope", "_strategy_version")

    def __init__(self, request_id: str, scope: HermesArmScope, strategy_version: str) -> None:
        self._request_id = request_id
        self._scope = scope
        self._strategy_version = strategy_version
        self._consumed = False

    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm:
        if not hmac.compare_digest(command.request_id, self._request_id):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
        if command.expected_scope.session_id != self._scope.session_id:
            raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION)
        if command.expected_scope.lane_id is not self._scope.lane_id:
            raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_LANE)
        if not hmac.compare_digest(expected_strategy_version, self._strategy_version):
            raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISMATCH)
        if self._consumed:
            raise InvalidHermesArmRequestError(HermesArmFailure.CONSUMED)
        self._consumed = True
        return PaperMutationArm(PAPER_MUTATION_ARM_VALUE)


@dataclass(frozen=True, slots=True)
class MintedPaperAutoArm:
    request_id: str
    consumer: SessionScopedPaperAutoArmConsumer


def mint_paper_auto_arm_consumer(
    policy: PaperAutoArmPolicy,
    authority: HermesArmAuthority,
    session_id: str,
    now: dt.datetime,
) -> MintedPaperAutoArm:
    scope = _current_intraday_scope(session_id, now)
    if authority.scope != scope:
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION)
    verify_paper_auto_arm_policy(policy, authority)
    request_id = paper_auto_arm_request_id(policy, session_id)
    return MintedPaperAutoArm(
        request_id=request_id,
        consumer=SessionScopedPaperAutoArmConsumer(request_id, scope, authority.strategy_version),
    )


def paper_auto_arm_request_id(policy: PaperAutoArmPolicy, session_id: str) -> str:
    material = "\0".join(("paper-auto-arm-request-v1", session_id, canonical_paper_auto_arm_policy_json(policy)))
    return hashlib.sha256(material.encode()).hexdigest()


def _current_intraday_scope(session_id: str, now: dt.datetime) -> HermesArmScope:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION)
    try:
        session_date = dt.date.fromisoformat(session_id.removeprefix("XNYS-"))
    except ValueError:
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION) from None
    if (
        session_id != f"XNYS-{session_date.isoformat()}"
        or now.astimezone(NEW_YORK).date() != session_date
        or regular_session_bounds(session_date) is None
    ):
        raise InvalidHermesArmRequestError(HermesArmFailure.WRONG_SESSION)
    return HermesArmScope(session_id=session_id, lane_id=LaneId.INTRADAY_MOMENTUM)
