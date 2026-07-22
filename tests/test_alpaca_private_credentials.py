from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.alpaca_private_credentials as credentials_module


def test_private_credential_loader_reads_private_regular_file(tmp_path: Path) -> None:
    # Given: a valid private credential file.
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)

    # When: the private loader reads the file.
    credentials = credentials_module.load_private_alpaca_credentials(secret)

    # Then: it returns the typed values without changing their private source.
    assert credentials.key_id == "test-key"
    assert credentials.secret_key == "test-secret"


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


def test_private_credential_loader_rejects_symlinked_parent(tmp_path: Path) -> None:
    # Given: a private credential file reached through an aliased directory component.
    real_parent = tmp_path / "real"
    real_parent.mkdir(mode=0o700)
    secret = real_parent / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    # When/Then: no ancestor symlink can become credential authority.
    with pytest.raises(credentials_module.PrivateAlpacaCredentialsError):
        _ = credentials_module.load_private_alpaca_credentials(alias / "alpaca.env")
