from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KrSupervisorPayloadSpec:
    interpreter: Path
    current_main_entrypoint: Path
    manifest: Path
    phase_epochs: tuple[int, int, int, int, int, int]
    request_sha256: str
    plan_sha256: str
    ledger_identity_sha256: str
    rollover_bundle_sha256: str
    policy_sha256: str


def render_kr_supervisor_payload(spec: KrSupervisorPayloadSpec) -> str:
    epochs = " ".join(str(epoch) for epoch in spec.phase_epochs)
    command = shlex.join(
        (
            str(spec.interpreter),
            str(spec.current_main_entrypoint),
            "supervise-kr-preflight",
            "--manifest",
            str(spec.manifest),
        )
    )
    return f"""#!/bin/zsh

set -u
umask 077

readonly request_sha256={spec.request_sha256}
readonly plan_sha256={spec.plan_sha256}
readonly ledger_identity_sha256={spec.ledger_identity_sha256}
readonly rollover_bundle_sha256={spec.rollover_bundle_sha256}
readonly policy_sha256={spec.policy_sha256}
readonly -a internal_phase_epochs=({epochs})

if (( ${{#internal_phase_epochs}} != 6 )); then
  print -u2 -r -- '{{"reason":"invalid_internal_phase_count","result":"blocked"}}'
  exit 78
fi

exec {command}
"""


__all__ = ("KrSupervisorPayloadSpec", "render_kr_supervisor_payload")
