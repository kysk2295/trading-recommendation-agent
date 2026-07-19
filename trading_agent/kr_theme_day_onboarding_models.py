from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionManifest,
    KrThemeDaySessionPaths,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_query_file import read_private_text_query_only

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KST = dt.timezone(dt.timedelta(hours=9))


class InvalidKrThemeDayOpportunityOnboardingError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day Opportunity onboarding is invalid"


class KrThemeDayOpportunityOnboardingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_path: Path
    paths: KrThemeDaySessionPaths
    trial_id: str
    opportunity_id: str
    onboarded_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.manifest_path.is_absolute()
            or _IDENTIFIER.fullmatch(self.trial_id) is None
            or _IDENTIFIER.fullmatch(self.opportunity_id) is None
            or not _aware(self.onboarded_at)
        ):
            raise InvalidKrThemeDayOpportunityOnboardingError
        return self


class KrThemeDayOpportunityOnboardingReceiptSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    trial_registration_key: str
    composite_registration_key: str
    session_id: str
    day_strategy_version: str
    opportunity_strategy_version: str
    opportunity_id: str
    opportunity_sha256: str
    source_cycle_id: str
    symbol: str
    session_date: dt.date
    registered_at: dt.datetime
    onboarded_at: dt.datetime
    calendar_snapshot_id: str

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        identifiers = (
            self.trial_id,
            self.day_strategy_version,
            self.opportunity_strategy_version,
            self.opportunity_id,
            self.source_cycle_id,
        )
        hashes = (
            self.trial_registration_key,
            self.composite_registration_key,
            self.session_id,
            self.opportunity_sha256,
            self.calendar_snapshot_id,
        )
        local = self.onboarded_at.astimezone(_KST) if _aware(self.onboarded_at) else self.onboarded_at
        if (
            any(_IDENTIFIER.fullmatch(value) is None for value in identifiers)
            or any(_HEX64.fullmatch(value) is None for value in hashes)
            or not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.registered_at)
            or not _aware(self.onboarded_at)
            or self.registered_at >= self.onboarded_at
            or local.date() != self.session_date
            or not dt.time(9) <= local.time() < dt.time(15, 30)
        ):
            raise InvalidKrThemeDayOpportunityOnboardingError
        return self


class KrThemeDayOpportunityOnboardingReceipt(KrThemeDayOpportunityOnboardingReceiptSource):
    schema_version: Literal[1] = 1
    receipt_id: str

    @model_validator(mode="after")
    def validate_receipt_id(self) -> Self:
        if self.receipt_id != _receipt_id(self):
            raise InvalidKrThemeDayOpportunityOnboardingError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayOpportunityOnboardingResult:
    created: bool
    manifest: KrThemeDaySessionManifest
    receipt: KrThemeDayOpportunityOnboardingReceipt


def build_kr_theme_day_onboarding_receipt(
    source: KrThemeDayOpportunityOnboardingReceiptSource,
) -> KrThemeDayOpportunityOnboardingReceipt:
    validated = KrThemeDayOpportunityOnboardingReceiptSource.model_validate(source.model_dump(mode="python"))
    provisional = KrThemeDayOpportunityOnboardingReceipt.model_construct(
        schema_version=1,
        receipt_id="0" * 64,
        **validated.model_dump(mode="python"),
    )
    return KrThemeDayOpportunityOnboardingReceipt.model_validate(
        provisional.model_dump(mode="python") | {"receipt_id": _receipt_id(provisional)}
    )


def onboarding_receipt_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f"{manifest_path.stem}.onboarding.json")


def write_kr_theme_day_onboarding_receipt(
    path: Path,
    receipt: KrThemeDayOpportunityOnboardingReceipt,
) -> bool:
    try:
        validated = KrThemeDayOpportunityOnboardingReceipt.model_validate(receipt.model_dump(mode="python"))
        target = path.expanduser().absolute()
        created = publish_private_immutable_text(target, _canonical(validated) + "\n")
        if not created and load_kr_theme_day_onboarding_receipt(target) != validated:
            raise InvalidKrThemeDayOpportunityOnboardingError
        return created
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpportunityOnboardingError from None


def load_kr_theme_day_onboarding_receipt(path: Path) -> KrThemeDayOpportunityOnboardingReceipt:
    try:
        target = path.expanduser().absolute()
        return _parse_receipt(read_private_text(target))
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpportunityOnboardingError from None


def load_kr_theme_day_onboarding_receipt_query_only(path: Path) -> KrThemeDayOpportunityOnboardingReceipt:
    try:
        target = path.expanduser().absolute()
        return _parse_receipt(read_private_text_query_only(target))
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpportunityOnboardingError from None


def _parse_receipt(payload: str) -> KrThemeDayOpportunityOnboardingReceipt:
    receipt = KrThemeDayOpportunityOnboardingReceipt.model_validate_json(payload)
    if payload != _canonical(receipt) + "\n":
        raise InvalidKrThemeDayOpportunityOnboardingError
    return receipt


def _receipt_id(receipt: KrThemeDayOpportunityOnboardingReceipt) -> str:
    payload = receipt.model_dump(mode="json", exclude={"receipt_id"})
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(receipt: KrThemeDayOpportunityOnboardingReceipt) -> str:
    return json.dumps(receipt.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
