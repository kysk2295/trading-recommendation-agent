from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    dynamic_subscription_request_bytes,
)
from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
)
from trading_agent.private_report import write_private_report
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_rules import base_is_current

_MANIFEST_ID = re.compile(
    r"alpaca-sip-actionability-manifest:[0-9a-f]{64}",
    flags=re.ASCII,
)


class AlpacaSipQuoteActionabilityManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability manifest is invalid"


class AlpacaSipQuoteActionabilityManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    scan_started_at: dt.datetime
    base_publication: TradeSignalPublication
    snapshot: IntradayFeatureSnapshot
    plan: AlpacaSipDynamicSubscriptionPlan

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        try:
            _ = dynamic_subscription_request_bytes(self.plan)
            scan_started_at = _scan_time(self.scan_started_at)
            matches = tuple(
                binding for binding in self.plan.bindings if binding.instrument_id == self.snapshot.instrument_id
            )
            valid = (
                _MANIFEST_ID.fullmatch(self.manifest_id) is not None
                and type(self.snapshot) is IntradayFeatureSnapshot
                and self.snapshot.status is FeatureSnapshotStatus.READY
                and self.plan.evaluated_at <= self.snapshot.observed_at
                and len(matches) == 1
                and matches[0].symbol == self.base_publication.signal.symbol
                and base_is_current(
                    self.base_publication,
                    scan_started_at=scan_started_at,
                    evaluated_at=self.snapshot.observed_at,
                )
                and self.manifest_id == _manifest_identity(self)
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise AlpacaSipQuoteActionabilityManifestError
        return self


def build_alpaca_sip_quote_actionability_manifest(
    base: TradeSignalPublication,
    snapshot: IntradayFeatureSnapshot,
    plan: AlpacaSipDynamicSubscriptionPlan,
    *,
    scan_started_at: dt.datetime,
) -> AlpacaSipQuoteActionabilityManifest:
    provisional = AlpacaSipQuoteActionabilityManifest.model_construct(
        manifest_id="alpaca-sip-actionability-manifest:" + "0" * 64,
        scan_started_at=scan_started_at,
        base_publication=base,
        snapshot=snapshot,
        plan=plan,
    )
    return AlpacaSipQuoteActionabilityManifest(
        manifest_id=_manifest_identity(provisional),
        scan_started_at=scan_started_at,
        base_publication=base,
        snapshot=snapshot,
        plan=plan,
    )


def write_alpaca_sip_quote_actionability_manifest(
    path: Path,
    manifest: AlpacaSipQuoteActionabilityManifest,
) -> bool:
    destination = path.expanduser().absolute()
    try:
        payload = _manifest_bytes(manifest)
        if destination.is_symlink():
            raise AlpacaSipQuoteActionabilityManifestError
        if destination.exists():
            existing = read_alpaca_sip_quote_actionability_manifest(destination)
            if existing != manifest:
                raise AlpacaSipQuoteActionabilityManifestError
            return False
        write_private_report(destination, payload.decode("ascii"))
        _require_private_file(destination)
        return True
    except (OSError, TypeError, UnicodeError, ValueError):
        raise AlpacaSipQuoteActionabilityManifestError from None


def read_alpaca_sip_quote_actionability_manifest(
    path: Path,
) -> AlpacaSipQuoteActionabilityManifest:
    source = path.expanduser().absolute()
    try:
        _require_private_file(source)
        payload = source.read_bytes()
        manifest = AlpacaSipQuoteActionabilityManifest.model_validate_json(payload)
        if _manifest_bytes(manifest) != payload:
            raise AlpacaSipQuoteActionabilityManifestError
        return manifest
    except (OSError, TypeError, ValidationError, ValueError):
        raise AlpacaSipQuoteActionabilityManifestError from None


def _manifest_identity(manifest: AlpacaSipQuoteActionabilityManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload["manifest_id"] = ""
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    return f"alpaca-sip-actionability-manifest:{digest}"


def _manifest_bytes(manifest: AlpacaSipQuoteActionabilityManifest) -> bytes:
    payload = manifest.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _scan_time(value: dt.datetime) -> dt.datetime:
    if type(value) is not dt.datetime or value.tzinfo is None or value.utcoffset() is None:
        raise AlpacaSipQuoteActionabilityManifestError
    return value


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipQuoteActionabilityManifestError


__all__ = (
    "AlpacaSipQuoteActionabilityManifest",
    "AlpacaSipQuoteActionabilityManifestError",
    "build_alpaca_sip_quote_actionability_manifest",
    "read_alpaca_sip_quote_actionability_manifest",
    "write_alpaca_sip_quote_actionability_manifest",
)
