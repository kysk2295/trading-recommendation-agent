from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.lane_contract_keys import canonical_lane_contract_json
from trading_agent.lane_identity_models import LaneId
from trading_agent.lane_policy_models import LaneRiskContract

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class HermesArmFailure(StrEnum):
    WRONG_OWNER = "wrong_owner"
    WRONG_SESSION = "wrong_session"
    WRONG_LANE = "wrong_lane"
    RISK_MISMATCH = "risk_mismatch"
    ACCOUNT_MISMATCH = "account_mismatch"
    COMMIT_MISMATCH = "commit_mismatch"
    CHAMPION_MISSING = "champion_missing"
    CHAMPION_MISMATCH = "champion_mismatch"
    DIRTY_COMMIT = "dirty_commit"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    CONFIRMATION_REPLAYED = "confirmation_replayed"
    INVALID_CONFIRMATION = "invalid_confirmation"
    NOT_CONFIRMED = "not_confirmed"
    INVALID_SIGNING_KEY = "invalid_signing_key"
    INVALID_STORE = "invalid_store"
    INVALID_REQUEST = "invalid_request"


class HermesArmTransitionKind(StrEnum):
    CONFIRMED = "confirmed"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvalidHermesArmRequestError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: HermesArmFailure) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason.value


class HermesArmScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    session_id: str
    lane_id: LaneId

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if _IDENTIFIER.fullmatch(self.session_id) is None:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
        return self


class HermesArmAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    scope: HermesArmScope
    strategy_version: str
    account_fingerprint: str = Field(repr=False)
    risk_contract_hash: str
    commit_sha: str
    champion_binding_key: str | None

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        hashes = (self.account_fingerprint, self.risk_contract_hash)
        if (
            _IDENTIFIER.fullmatch(self.strategy_version) is None
            or any(_SHA256.fullmatch(value) is None for value in hashes)
            or _GIT_SHA.fullmatch(self.commit_sha) is None
            or (self.champion_binding_key is not None and _SHA256.fullmatch(self.champion_binding_key) is None)
        ):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
        return self

    @staticmethod
    def risk_hash(contract: LaneRiskContract) -> str:
        return hashlib.sha256(canonical_lane_contract_json(contract).encode()).hexdigest()


class HermesArmPrepareCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    owner_id_hash: str = Field(repr=False)
    scope: HermesArmScope


class HermesArmConfirmCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    owner_id_hash: str = Field(repr=False)
    request_id: str
    confirmation: str = Field(repr=False)


class HermesArmConsumeCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    request_id: str
    expected_scope: HermesArmScope


class HermesArmRevokeCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    owner_id_hash: str = Field(repr=False)
    request_id: str


class HermesArmRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    request_id: str
    owner_id_hash: str = Field(repr=False)
    authority: HermesArmAuthority
    nonce_hash: str = Field(repr=False)
    confirmation_hash: str = Field(repr=False)
    prepared_at: AwareDatetime
    expires_at: AwareDatetime
    signature: str = Field(repr=False)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        hashes = (self.request_id, self.owner_id_hash, self.nonce_hash, self.confirmation_hash, self.signature)
        if any(_SHA256.fullmatch(value) is None for value in hashes) or self.expires_at <= self.prepared_at:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_REQUEST)
        return self


class PreparedHermesArm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    request_id: str
    confirmation: str = Field(repr=False)
    expires_at: AwareDatetime


class HermesArmRequestStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    request_id: str
    status: HermesArmTransitionKind | None
    expires_at: AwareDatetime


class HermesArmTransition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    request_id: str
    sequence: int
    kind: HermesArmTransitionKind
    occurred_at: AwareDatetime
    previous_signature: str | None = Field(default=None, repr=False)
    signature: str = Field(repr=False)
