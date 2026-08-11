from __future__ import annotations

import hmac
import json
import os
import stat
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alpaca_paper_contract import ALPACA_PAPER_TRADING_URL
from trading_agent.hermes_arm_request import HermesArmAuthority
from trading_agent.lane_identity_models import LaneId


class PaperAutoArmPolicyFailure(StrEnum):
    INVALID_FILE = "invalid_file"
    DISABLED = "disabled"
    WRONG_LANE = "wrong_lane"
    ACCOUNT_MISMATCH = "account_mismatch"
    RISK_MISMATCH = "risk_mismatch"
    COMMIT_MISMATCH = "commit_mismatch"
    CHAMPION_MISSING = "champion_missing"
    CHAMPION_MISMATCH = "champion_mismatch"


class InvalidPaperAutoArmPolicyError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: PaperAutoArmPolicyFailure) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason.value


class PaperAutoArmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    enabled: bool
    lane_id: Literal[LaneId.INTRADAY_MOMENTUM]
    paper_base_url: Literal["https://paper-api.alpaca.markets"] = ALPACA_PAPER_TRADING_URL
    account_fingerprint: str = Field(repr=False, pattern=r"^[0-9a-f]{64}$")
    risk_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_version: str = Field(min_length=1, max_length=128)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    champion_binding_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_strategy_version(self) -> Self:
        if any(character.isspace() for character in self.strategy_version):
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
        return self

    @classmethod
    def from_authority(cls, authority: HermesArmAuthority) -> Self:
        if authority.scope.lane_id is not LaneId.INTRADAY_MOMENTUM:
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.WRONG_LANE)
        if authority.champion_binding_key is None:
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.CHAMPION_MISSING)
        return cls(
            enabled=True,
            lane_id=LaneId.INTRADAY_MOMENTUM,
            account_fingerprint=authority.account_fingerprint,
            risk_contract_hash=authority.risk_contract_hash,
            strategy_version=authority.strategy_version,
            commit_sha=authority.commit_sha,
            champion_binding_key=authority.champion_binding_key,
        )


def canonical_paper_auto_arm_policy_json(policy: PaperAutoArmPolicy) -> str:
    return json.dumps(policy.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def write_paper_auto_arm_policy(path: Path, policy: PaperAutoArmPolicy) -> None:
    _require_secure_parent(path.parent)
    if os.path.lexists(path):
        if load_paper_auto_arm_policy(path) == policy:
            return
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(canonical_paper_auto_arm_policy_json(policy) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except OSError:
        temporary.unlink(missing_ok=True)
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE) from None


def load_paper_auto_arm_policy(path: Path) -> PaperAutoArmPolicy:
    try:
        _require_secure_parent(path.parent)
        before = path.lstat()
        if not _secure_file_metadata(before) or path.is_symlink():
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not _same_file(before, opened) or not _secure_file_metadata(opened):
            os.close(descriptor)
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            raw = handle.read(4097)
            after_read = os.fstat(handle.fileno())
        after = path.lstat()
        if len(raw) > 4096 or not _stable_file(opened, after_read, after):
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
        policy = PaperAutoArmPolicy.model_validate_json(raw)
        if raw != canonical_paper_auto_arm_policy_json(policy) + "\n":
            raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)
        return policy
    except InvalidPaperAutoArmPolicyError:
        raise
    except (OSError, UnicodeError, ValidationError):
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE) from None


def _require_secure_parent(parent: Path) -> None:
    try:
        metadata = parent.lstat()
    except OSError:
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
        or parent.is_symlink()
    ):
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.INVALID_FILE)


def _secure_file_metadata(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
    )


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _stable_file(
    opened: os.stat_result,
    after_read: os.stat_result,
    after_path: os.stat_result,
) -> bool:
    identity_is_stable = _same_file(opened, after_read) and _same_file(opened, after_path)
    content_is_stable = (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) == (
        after_read.st_size,
        after_read.st_mtime_ns,
        after_read.st_ctime_ns,
    )
    return identity_is_stable and content_is_stable and _secure_file_metadata(after_path)


def verify_paper_auto_arm_policy(policy: PaperAutoArmPolicy, authority: HermesArmAuthority) -> None:
    if not policy.enabled:
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.DISABLED)
    if authority.scope.lane_id is not LaneId.INTRADAY_MOMENTUM:
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.WRONG_LANE)
    checks = (
        (policy.account_fingerprint, authority.account_fingerprint, PaperAutoArmPolicyFailure.ACCOUNT_MISMATCH),
        (policy.risk_contract_hash, authority.risk_contract_hash, PaperAutoArmPolicyFailure.RISK_MISMATCH),
        (policy.commit_sha, authority.commit_sha, PaperAutoArmPolicyFailure.COMMIT_MISMATCH),
        (policy.strategy_version, authority.strategy_version, PaperAutoArmPolicyFailure.CHAMPION_MISMATCH),
    )
    for actual, expected, reason in checks:
        if not hmac.compare_digest(actual, expected):
            raise InvalidPaperAutoArmPolicyError(reason)
    binding = authority.champion_binding_key
    if binding is None:
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.CHAMPION_MISSING)
    if not hmac.compare_digest(policy.champion_binding_key, binding):
        raise InvalidPaperAutoArmPolicyError(PaperAutoArmPolicyFailure.CHAMPION_MISMATCH)
