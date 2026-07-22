from __future__ import annotations

import hashlib
import hmac
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from trading_agent.hermes_arm_request import HermesArmFailure, InvalidHermesArmRequestError

DEFAULT_HERMES_ARM_SIGNING_KEY_PATH: Final = Path("~/.config/trading-agent/hermes-arm.env")
_KEY_NAME: Final = "HERMES_ARM_SIGNING_KEY"


@dataclass(frozen=True, slots=True)
class HermesArmSigningKey:
    value: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class HermesArmSigner:
    _key: HermesArmSigningKey = field(repr=False)

    @classmethod
    def from_bytes(cls, value: bytes) -> HermesArmSigner:
        if len(value) < 32:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_SIGNING_KEY)
        return cls(HermesArmSigningKey(value))

    def sign(self, payload: str) -> str:
        return hmac.new(self._key.value, payload.encode(), hashlib.sha256).hexdigest()

    def confirmation(self, nonce: bytes, request_material: str) -> str:
        material = nonce + b"\0" + request_material.encode()
        return hmac.new(self._key.value, material, hashlib.sha256).hexdigest()

    def verify(self, payload: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature)


def load_hermes_arm_signing_key(path: Path = DEFAULT_HERMES_ARM_SIGNING_KEY_PATH) -> HermesArmSigningKey:
    resolved = path.expanduser()
    descriptor = -1
    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        secure = (
            stat.S_ISREG(details.st_mode)
            and details.st_uid == os.getuid()
            and stat.S_IMODE(details.st_mode) == 0o600
        )
        if not secure:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_SIGNING_KEY)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            lines = handle.read().splitlines()
    except OSError:
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_SIGNING_KEY) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(lines) != 1:
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_SIGNING_KEY)
    name, separator, value = lines[0].partition("=")
    if name != _KEY_NAME or separator != "=" or len(value.encode()) < 32:
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_SIGNING_KEY)
    return HermesArmSigningKey(value.encode())
