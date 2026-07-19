from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from pydantic import BaseModel

from trading_agent.kr_same_cycle_opportunity_models import (
    InvalidKrSameCycleOpportunityRunError,
    KrSameCycleOpportunityPolicy,
    KrSameCycleOpportunityPreparation,
    PreparedKrSameCycleOpportunityRun,
)
from trading_agent.kr_theme_projection_manifest import (
    KrThemeProjectionRunManifest,
    load_kr_theme_projection_run,
)

RUN_MANIFEST_NAME = "projection-run.json"
_POLICY_NAME = "policy.json"
_RULES_NAME = "keyword-rules.json"


def opportunity_bundle_path(request: KrSameCycleOpportunityPreparation) -> Path:
    digest = hashlib.sha256(request.collection_cycle_id.encode()).hexdigest()
    return request.run_root / f"kr-opportunity-{digest[:24]}"


def write_opportunity_bundle(
    bundle: Path,
    request: KrSameCycleOpportunityPreparation,
    policy: KrSameCycleOpportunityPolicy,
) -> None:
    root = _private_directory(request.run_root)
    if bundle.parent != root or bundle.is_symlink():
        raise InvalidKrSameCycleOpportunityRunError
    if bundle.exists():
        raise InvalidKrSameCycleOpportunityRunError
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{bundle.name}.",
            suffix=".staging",
            dir=root,
        )
    )
    stage.chmod(0o700)
    run = KrThemeProjectionRunManifest(
        collection_cycle_id=request.collection_cycle_id,
        rules_path=_RULES_NAME,
        classification_run_id=_classification_run_id(request, policy),
        classified_at=request.prepared_at,
        projected_at=request.prepared_at,
        validity_seconds=policy.validity_seconds,
        producer_strategy_version=policy.producer_strategy_version,
        runtime_code_version=policy.runtime_code_version,
    )
    try:
        _write_exact_private(stage / _POLICY_NAME, _canonical_bytes(policy))
        _write_exact_private(stage / _RULES_NAME, _canonical_bytes(policy.rules))
        _write_exact_private(stage / RUN_MANIFEST_NAME, _canonical_bytes(run))
        _sync_directory(stage)
        try:
            stage.rename(bundle)
        except FileExistsError:
            return
        _sync_directory(root)
    finally:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()


def load_opportunity_bundle(
    bundle: Path,
    request: KrSameCycleOpportunityPreparation,
    policy: KrSameCycleOpportunityPolicy,
    *,
    replayed: bool,
) -> PreparedKrSameCycleOpportunityRun:
    _require_private_directory(bundle)
    policy_path = bundle / _POLICY_NAME
    rules_path = bundle / _RULES_NAME
    run_path = bundle / RUN_MANIFEST_NAME
    for path in (policy_path, rules_path, run_path):
        _require_private_file(path)
    stored_policy = KrSameCycleOpportunityPolicy.model_validate_json(policy_path.read_bytes())
    loaded = load_kr_theme_projection_run(run_path)
    run = loaded.run
    if (
        policy_path.read_bytes() != _canonical_bytes(stored_policy)
        or rules_path.read_bytes() != _canonical_bytes(loaded.rules)
        or run_path.read_bytes() != _canonical_bytes(run)
        or stored_policy != policy
        or loaded.rules != policy.rules
        or run.collection_cycle_id != request.collection_cycle_id
        or run.classification_run_id != _classification_run_id(request, policy)
        or run.validity_seconds != policy.validity_seconds
        or run.producer_strategy_version != policy.producer_strategy_version
        or run.runtime_code_version != policy.runtime_code_version
        or run.classified_at != run.projected_at
    ):
        raise InvalidKrSameCycleOpportunityRunError
    return PreparedKrSameCycleOpportunityRun(run_path, loaded, replayed)


def _classification_run_id(
    request: KrSameCycleOpportunityPreparation,
    policy: KrSameCycleOpportunityPolicy,
) -> str:
    payload = (
        request.collection_cycle_id,
        policy.rules.classifier_version,
        policy.rules.prompt_version,
        policy.producer_strategy_version,
    )
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    return f"kr-live-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _private_directory(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    if candidate.is_symlink():
        raise InvalidKrSameCycleOpportunityRunError
    if not candidate.exists():
        candidate.mkdir(parents=True, mode=0o700)
        candidate.chmod(0o700)
    _require_private_directory(candidate)
    return candidate


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InvalidKrSameCycleOpportunityRunError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrSameCycleOpportunityRunError


def _write_exact_private(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _require_private_file(path)
        if path.read_bytes() != content:
            raise InvalidKrSameCycleOpportunityRunError from None
        return
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "RUN_MANIFEST_NAME",
    "load_opportunity_bundle",
    "opportunity_bundle_path",
    "write_opportunity_bundle",
)
