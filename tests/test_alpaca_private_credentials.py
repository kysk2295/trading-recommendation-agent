from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.alpaca_private_credentials as credentials_module


def test_private_credential_loader_uses_no_follow_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid private credential file and an observable OS open boundary.
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    observed_flags: list[int] = []
    real_open = os.open

    def tracked_open(path: Path, flags: int) -> int:
        observed_flags.append(flags)
        return real_open(path, flags)

    monkeypatch.setattr(credentials_module.os, "open", tracked_open)

    # When: the private loader reads the file.
    credentials = credentials_module.load_private_alpaca_credentials(secret)

    # Then: it opens through an OS no-follow descriptor and returns redacted values.
    assert credentials.key_id == "test-key"
    assert credentials.secret_key == "test-secret"
    assert observed_flags
    assert observed_flags[0] & os.O_NOFOLLOW


def test_private_credential_loader_rejects_symlink_and_hard_link(tmp_path: Path) -> None:
    # Given: a private-looking credential file exposed through two unsafe aliases.
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    symlink = tmp_path / "symlink.env"
    symlink.symlink_to(secret)
    hard_link = tmp_path / "hard-link.env"
    os.link(secret, hard_link)

    # When/Then: neither alias can be used as credential authority.
    with pytest.raises(credentials_module.PrivateAlpacaCredentialsError):
        _ = credentials_module.load_private_alpaca_credentials(symlink)
    with pytest.raises(credentials_module.PrivateAlpacaCredentialsError):
        _ = credentials_module.load_private_alpaca_credentials(hard_link)
