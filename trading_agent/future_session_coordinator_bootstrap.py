from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.future_session_coordinator_inspectors import inspect_request
from trading_agent.future_session_coordinator_service_models import (
    MAX_COORDINATOR_POLL_SECONDS,
    MIN_COORDINATOR_POLL_SECONDS,
    FutureSessionCoordinatorServiceConfig,
    canonical_service_config_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    load_service_config,
)
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanRequest,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_verifier import read_private_file
from trading_agent.future_session_us_materializer_io import write_private_file
from trading_agent.repository_current_main import CurrentMainAuthorityError, current_main_commit


class InvalidFutureSessionCoordinatorBootstrapManifestError(ValueError):
    pass


class FutureSessionCoordinatorBootstrapManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    bundle_path: Path
    state_root: Path
    launch_agents_dir: Path
    authority_repository: Path
    scheduler_main_sha: str
    poll_interval_seconds: int
    us_template: FutureSessionPlanRequest
    kr_template: FutureSessionPlanRequest

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        paths = (
            self.bundle_path,
            self.state_root,
            self.launch_agents_dir,
            self.authority_repository,
        )
        lexical = tuple(Path(os.path.abspath(path)) for path in paths)
        try:
            resolved = tuple(path.resolve(strict=False) for path in paths)
        except (OSError, RuntimeError):
            raise InvalidFutureSessionCoordinatorBootstrapManifestError from None
        if (
            any(not path.is_absolute() for path in paths)
            or lexical != paths
            or resolved != paths
            or _roots_overlap(resolved)
            or not (MIN_COORDINATOR_POLL_SECONDS <= self.poll_interval_seconds <= MAX_COORDINATOR_POLL_SECONDS)
            or self.us_template.market is not FutureSessionMarket.US
            or self.kr_template.market is not FutureSessionMarket.KR
            or self.us_template.scheduler_main_sha != self.scheduler_main_sha
            or self.kr_template.scheduler_main_sha != self.scheduler_main_sha
            or self.us_template.authority_repository != self.authority_repository
            or self.kr_template.authority_repository != self.authority_repository
        ):
            raise InvalidFutureSessionCoordinatorBootstrapManifestError
        return self


def canonical_bootstrap_manifest_json(
    manifest: FutureSessionCoordinatorBootstrapManifest,
) -> str:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def load_bootstrap_manifest(path: Path) -> FutureSessionCoordinatorBootstrapManifest:
    try:
        payload = read_private_file(path, 0o600)
        manifest = FutureSessionCoordinatorBootstrapManifest.model_validate_json(payload)
    except (OSError, TypeError, ValidationError, ValueError):
        raise FrozenRuntimeError("invalid_bootstrap_manifest") from None
    if canonical_bootstrap_manifest_json(manifest).encode() != payload:
        raise FrozenRuntimeError("invalid_bootstrap_manifest")
    return manifest


def bootstrap_coordinator_bundle(
    manifest: FutureSessionCoordinatorBootstrapManifest,
) -> Path:
    try:
        if current_main_commit(manifest.authority_repository) != manifest.scheduler_main_sha:
            raise FrozenRuntimeError("configured_main_authority_mismatch")
    except CurrentMainAuthorityError:
        raise FrozenRuntimeError("current_main_authority_invalid") from None
    config = _service_config(manifest)
    expected = _expected_files(manifest, config)
    destination = manifest.bundle_path
    _ensure_private_directory(destination.parent)
    if destination.exists():
        _verify_bundle(destination, expected)
        return destination / "coordinator.json"
    stage = destination.parent / f".{destination.name}.creating-{os.getpid()}"
    if os.path.lexists(stage):
        raise FrozenRuntimeError("bootstrap_publication_conflict")
    try:
        stage.mkdir(mode=0o700)
        for name, payload in expected.items():
            write_private_file(stage / name, payload, 0o600)
        _fsync_directory(stage)
        stage.rename(destination)
        _fsync_directory(destination.parent)
        _verify_bundle(destination, expected)
    except (FrozenRuntimeError, OSError, TypeError, ValueError):
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise FrozenRuntimeError("bootstrap_publication_invalid") from None
    return destination / "coordinator.json"


def _service_config(
    manifest: FutureSessionCoordinatorBootstrapManifest,
) -> FutureSessionCoordinatorServiceConfig:
    us_template_sha256 = hashlib.sha256(canonical_request_json(manifest.us_template).encode()).hexdigest()
    kr_template_sha256 = hashlib.sha256(canonical_request_json(manifest.kr_template).encode()).hexdigest()
    return FutureSessionCoordinatorServiceConfig(
        us_template_request_path=manifest.bundle_path / "us-template.json",
        kr_template_request_path=manifest.bundle_path / "kr-template.json",
        us_template_sha256=us_template_sha256,
        kr_template_sha256=kr_template_sha256,
        state_root=manifest.state_root,
        launch_agents_dir=manifest.launch_agents_dir,
        authority_repository=manifest.authority_repository,
        scheduler_main_sha=manifest.scheduler_main_sha,
        poll_interval_seconds=manifest.poll_interval_seconds,
    )


def _expected_files(
    manifest: FutureSessionCoordinatorBootstrapManifest,
    config: FutureSessionCoordinatorServiceConfig,
) -> dict[str, bytes]:
    return {
        "coordinator.json": canonical_service_config_json(config).encode(),
        "us-template.json": canonical_request_json(manifest.us_template).encode(),
        "kr-template.json": canonical_request_json(manifest.kr_template).encode(),
    }


def _verify_bundle(path: Path, expected: dict[str, bytes]) -> None:
    _require_private_directory(path)
    if {item.name for item in path.iterdir()} != set(expected):
        raise FrozenRuntimeError("bootstrap_bundle_invalid")
    for name, payload in expected.items():
        candidate = path / name
        if read_private_file(candidate, 0o600) != payload:
            raise FrozenRuntimeError("bootstrap_bundle_invalid")
    _ = load_service_config(path / "coordinator.json")
    _ = inspect_request(path / "us-template.json")
    _ = inspect_request(path / "kr-template.json")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(path)


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FrozenRuntimeError("private_bundle_directory_invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _roots_overlap(paths: tuple[Path, ...]) -> bool:
    return any(
        left == right or left.is_relative_to(right) or right.is_relative_to(left)
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    )


__all__ = (
    "FutureSessionCoordinatorBootstrapManifest",
    "InvalidFutureSessionCoordinatorBootstrapManifestError",
    "bootstrap_coordinator_bundle",
    "canonical_bootstrap_manifest_json",
    "load_bootstrap_manifest",
)
