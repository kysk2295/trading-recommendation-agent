from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import trading_agent.paper_auto_arm_policy as policy_module
from trading_agent.hermes_arm_request import HermesArmAuthority, HermesArmScope
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE


def test_policy_round_trip_is_canonical_mode_600_without_reusable_arm(tmp_path: Path) -> None:
    # Given: current Paper authority and an absent standing-policy path.
    authority = _authority()
    policy_path = tmp_path / "paper-auto-arm.json"

    # When: the authority is provisioned and loaded through the secure boundary.
    policy = policy_module.PaperAutoArmPolicy.from_authority(authority)
    policy_module.write_paper_auto_arm_policy(policy_path, policy)
    loaded = policy_module.load_paper_auto_arm_policy(policy_path)

    # Then: bytes are canonical, protected, and contain no reusable mutation arm.
    assert loaded == policy
    assert stat.S_IMODE(policy_path.stat().st_mode) == 0o600
    assert policy_path.read_text(encoding="utf-8") == policy_module.canonical_paper_auto_arm_policy_json(policy) + "\n"
    assert PAPER_MUTATION_ARM_VALUE not in policy_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "field,replacement,reason",
    (
        ("enabled", False, "disabled"),
        ("account_fingerprint", "1" * 64, "account_mismatch"),
        ("risk_contract_hash", "2" * 64, "risk_mismatch"),
        ("strategy_version", "other-v1", "champion_mismatch"),
        ("commit_sha", "3" * 40, "commit_mismatch"),
        ("champion_binding_key", "4" * 64, "champion_mismatch"),
    ),
)
def test_policy_verification_rejects_each_stale_binding(
    field: str,
    replacement: bool | str,
    reason: str,
) -> None:
    # Given: one policy binding differs from current resolved authority.
    policy = policy_module.PaperAutoArmPolicy.from_authority(_authority()).model_copy(update={field: replacement})

    # When / Then: verification fails with a redacted stable reason.
    with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError) as blocked:
        policy_module.verify_paper_auto_arm_policy(policy, _authority())
    assert blocked.value.reason.value == reason


@pytest.mark.parametrize("mode", (0o644, 0o400, 0o700))
def test_policy_loader_rejects_non_600_mode(tmp_path: Path, mode: int) -> None:
    # Given: canonical policy bytes with an unsafe filesystem mode.
    policy_path = tmp_path / "paper-auto-arm.json"
    policy_path.write_text(
        policy_module.canonical_paper_auto_arm_policy_json(
            policy_module.PaperAutoArmPolicy.from_authority(_authority())
        ),
        encoding="utf-8",
    )
    policy_path.chmod(mode)

    # When / Then: loading fails without exposing authority material.
    with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError) as blocked:
        policy_module.load_paper_auto_arm_policy(policy_path)
    assert blocked.value.reason.value == "invalid_file"


def test_policy_loader_rejects_symlink_and_extra_fields(tmp_path: Path) -> None:
    # Given: one symlink and one noncanonical JSON policy.
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    extra = tmp_path / "extra.json"
    payload = policy_module.PaperAutoArmPolicy.from_authority(_authority()).model_dump(mode="json")
    payload["secret"] = "forbidden"
    extra.write_text(json.dumps(payload), encoding="utf-8")
    extra.chmod(0o600)

    # When / Then: both inputs fail closed at the policy boundary.
    for path in (link, extra):
        with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError) as blocked:
            policy_module.load_paper_auto_arm_policy(path)
        assert blocked.value.reason.value == "invalid_file"


def test_policy_writer_allows_exact_replay_but_rejects_conflicting_existing(tmp_path: Path) -> None:
    # Given: an existing canonical policy file.
    policy_path = tmp_path / "paper-auto-arm.json"
    policy = policy_module.PaperAutoArmPolicy.from_authority(_authority())
    policy_module.write_paper_auto_arm_policy(policy_path, policy)

    # When: exact replay and then conflicting provision are attempted.
    policy_module.write_paper_auto_arm_policy(policy_path, policy)
    conflict = policy.model_copy(update={"enabled": False})

    # Then: replay is idempotent while conflicting authority cannot overwrite it.
    with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError) as blocked:
        policy_module.write_paper_auto_arm_policy(policy_path, conflict)
    assert blocked.value.reason.value == "invalid_file"
    assert policy_module.load_paper_auto_arm_policy(policy_path) == policy


def test_policy_file_boundaries_reject_unsafe_parent_and_hard_link(tmp_path: Path) -> None:
    # Given: one unsafe policy directory and one multiply-linked policy file.
    unsafe_parent = tmp_path / "unsafe"
    unsafe_parent.mkdir(mode=0o755)
    unsafe_parent.chmod(0o755)
    safe_parent = tmp_path / "safe"
    safe_parent.mkdir(mode=0o700)
    policy = policy_module.PaperAutoArmPolicy.from_authority(_authority())
    policy_path = safe_parent / "paper-auto-arm.json"
    policy_module.write_paper_auto_arm_policy(policy_path, policy)
    hard_link = safe_parent / "copy.json"
    hard_link.hardlink_to(policy_path)

    # When / Then: provisioning and loading both fail closed.
    with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError):
        policy_module.write_paper_auto_arm_policy(unsafe_parent / "policy.json", policy)
    with pytest.raises(policy_module.InvalidPaperAutoArmPolicyError):
        policy_module.load_paper_auto_arm_policy(policy_path)


def _authority() -> HermesArmAuthority:
    return HermesArmAuthority(
        scope=HermesArmScope(session_id="XNYS-2026-07-14", lane_id=LaneId.INTRADAY_MOMENTUM),
        strategy_version="orb-v1",
        account_fingerprint="a" * 64,
        risk_contract_hash="b" * 64,
        commit_sha="c" * 40,
        champion_binding_key="d" * 64,
    )
