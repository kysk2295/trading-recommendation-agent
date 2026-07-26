from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from trading_agent.dashboard_executable_binding import (
    FileIdentity,
    InvalidExecutableBindingError,
    capture_file,
    revalidate,
    tree_sha256,
)
from trading_agent.dashboard_research_broker_contract import (
    BrokerOperation,
    InvalidResearchBrokerCommandError,
    decode_broker_command,
    encode_broker_command,
)

ExecutionRole = Literal[
    "hermes-model",
    "hermes-probe",
    "fixture-model",
    "health-broker",
    "research-broker",
]


@dataclass(frozen=True, slots=True)
class BoundExecutionRequest:
    identity: BoundExecutionIdentity
    role: ExecutionRole
    identity_digest: str
    template_digest: str
    prompt: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundExecutionIdentity:
    role: ExecutionRole
    executable: FileIdentity
    launcher: FileIdentity | None
    target: FileIdentity | None
    supporting_files: tuple[FileIdentity, ...]
    package_root: Path | None
    package_sha256: str | None
    readable_roots: tuple[Path, ...]
    readable_literals: tuple[Path, ...]
    identity_digest: str
    template_digest: str
    test_only: bool

    def request(self, prompt: str = "") -> BoundExecutionRequest:
        match self.role:
            case "health-broker":
                if prompt:
                    raise InvalidExecutableBindingError("broker_arguments_forbidden")
                argv = (str(self.executable.path),)
            case "research-broker":
                raise InvalidExecutableBindingError("broker_request_required")
            case "hermes-probe":
                if prompt:
                    raise InvalidExecutableBindingError("execution_probe_arguments_forbidden")
                if self.launcher is None or self.target is None:
                    raise InvalidExecutableBindingError("execution_identity_incomplete")
                argv = (
                    str(self.executable.path),
                    str(self.launcher.path),
                    self.role,
                    str(self.target.path),
                    "",
                )
            case "hermes-model" | "fixture-model":
                if not prompt or len(prompt.encode("utf-8")) > 32 * 1024:
                    raise InvalidExecutableBindingError("execution_prompt_invalid")
                if self.launcher is None or self.target is None:
                    raise InvalidExecutableBindingError("execution_identity_incomplete")
                argv = (
                    str(self.executable.path),
                    str(self.launcher.path),
                    self.role,
                    str(self.target.path),
                    prompt,
                )
            case unexpected:
                assert_never(unexpected)
        return BoundExecutionRequest(
            self,
            self.role,
            self.identity_digest,
            self.template_digest,
            prompt,
            argv,
        )

    def broker_request(
        self,
        operation: BrokerOperation,
        parameters: tuple[str, ...],
    ) -> BoundExecutionRequest:
        if self.role != "research-broker" or self.launcher is None or self.target is None:
            raise InvalidExecutableBindingError("research_broker_identity_required")
        try:
            payload = encode_broker_command(operation, parameters)
        except InvalidResearchBrokerCommandError as error:
            raise InvalidExecutableBindingError(error.reason) from error
        argv = (
            str(self.executable.path),
            str(self.launcher.path),
            self.role,
            str(self.target.path),
            payload,
        )
        operation_template_digest = _digest((self.template_digest, operation))
        return BoundExecutionRequest(
            self,
            self.role,
            self.identity_digest,
            operation_template_digest,
            payload,
            argv,
        )

    def revalidate(self) -> None:
        revalidate(self.executable, executable=True)
        if self.launcher is not None:
            revalidate(self.launcher, executable=False)
        if self.target is not None:
            revalidate(self.target, executable=True)
        for identity in self.supporting_files:
            revalidate(identity, executable=True)
        if self.package_root is not None and tree_sha256(self.package_root) != self.package_sha256:
            raise InvalidExecutableBindingError("trusted_package_identity_changed")

    def accepts(self, request: BoundExecutionRequest) -> bool:
        if request.identity is not self:
            return False
        try:
            if self.role == "research-broker":
                operation, parameters = decode_broker_command(request.prompt)
                expected = self.broker_request(operation, parameters)
            else:
                expected = self.request(request.prompt)
        except InvalidExecutableBindingError:
            return False
        except InvalidResearchBrokerCommandError:
            return False
        return request == expected


def _build_native_identity(executable: FileIdentity) -> BoundExecutionIdentity:
    template_digest = _digest(("health-broker", str(executable.path)))
    identity = BoundExecutionIdentity(
        "health-broker",
        executable,
        None,
        None,
        (),
        None,
        None,
        (),
        (),
        "",
        template_digest,
        False,
    )
    identity = BoundExecutionIdentity(
        identity.role,
        identity.executable,
        identity.launcher,
        identity.target,
        identity.supporting_files,
        identity.package_root,
        identity.package_sha256,
        identity.readable_roots,
        identity.readable_literals,
        _identity_digest(identity),
        identity.template_digest,
        identity.test_only,
    )
    return identity


def _build_python_identity(
    role: Literal["hermes-model", "hermes-probe", "fixture-model", "research-broker"],
    repository: Path,
    interpreter: FileIdentity,
    target: FileIdentity,
    package_root: Path | None,
    readable_roots: tuple[Path, ...],
    *,
    readable_literals: tuple[Path, ...],
    test_only: bool,
    supporting_files: tuple[FileIdentity, ...] = (),
) -> BoundExecutionIdentity:
    launcher = capture_file(
        repository / "trading_agent" / "dashboard_python_execution_guard.py",
        executable=False,
    )
    package_hash = tree_sha256(package_root) if package_root is not None else None
    template_digest = _digest(
        (
            str(interpreter.path),
            str(launcher.path),
            role,
            str(target.path),
            "{bounded_prompt}",
        )
    )
    identity = BoundExecutionIdentity(
        role,
        interpreter,
        launcher,
        target,
        supporting_files,
        package_root,
        package_hash,
        readable_roots,
        readable_literals,
        "",
        template_digest,
        test_only,
    )
    identity_digest = _identity_digest(identity)
    identity = BoundExecutionIdentity(
        identity.role,
        identity.executable,
        identity.launcher,
        identity.target,
        identity.supporting_files,
        identity.package_root,
        identity.package_sha256,
        identity.readable_roots,
        identity.readable_literals,
        identity_digest,
        identity.template_digest,
        identity.test_only,
    )
    return identity


def _digest(values: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def _identity_digest(identity: BoundExecutionIdentity) -> str:
    return _digest(
        (
            identity.template_digest,
            identity.executable.sha256,
            "" if identity.launcher is None else identity.launcher.sha256,
            "" if identity.target is None else identity.target.sha256,
            *(item.sha256 for item in identity.supporting_files),
            identity.package_sha256 or "",
            *(str(path) for path in identity.readable_literals),
        )
    )


__all__ = (
    "BoundExecutionIdentity",
    "BoundExecutionRequest",
    "BrokerOperation",
    "ExecutionRole",
)
