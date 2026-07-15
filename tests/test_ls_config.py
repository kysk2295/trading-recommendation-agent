from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.ls_config as ls_config
from trading_agent.ls_config import (
    DEFAULT_LS_SECRET_PATH,
    LS_REST_BASE_URL,
    InvalidLsCredentialsError,
    LsCredentials,
    LsSecretEncodingError,
    LsSecretFileError,
    create_ls_http_client,
    load_ls_credentials,
)

APP_KEY = "k" * 40
APP_SECRET = "s" * 40


def test_ls_config_uses_separate_private_secret_file() -> None:
    expected = Path.home() / ".config/trading-agent/ls.env"

    assert expected == DEFAULT_LS_SECRET_PATH


def test_load_ls_credentials_accepts_only_exact_private_file(
    tmp_path: Path,
) -> None:
    secret = _secret(
        tmp_path,
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
    )

    credentials = load_ls_credentials(secret)

    assert credentials.app_key == APP_KEY
    assert credentials.app_secret == APP_SECRET
    rendered = repr(credentials)
    assert APP_KEY not in rendered
    assert APP_SECRET not in rendered


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644, 0o700))
def test_ls_secret_rejects_every_mode_except_600(
    tmp_path: Path,
    mode: int,
) -> None:
    secret = _secret(
        tmp_path,
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
        mode=mode,
    )

    with pytest.raises(LsSecretFileError, match="600"):
        _ = load_ls_credentials(secret)


def test_ls_secret_rejects_symlink(tmp_path: Path) -> None:
    target = _secret(
        tmp_path,
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
    )
    link = tmp_path / "ls-link.env"
    link.symlink_to(target)

    with pytest.raises(LsSecretFileError):
        _ = load_ls_credentials(link)


def test_ls_secret_path_swap_cannot_replace_opened_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _secret(
        tmp_path,
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
    )
    replacement = tmp_path / "replacement.env"
    replacement.write_text(
        f"LS_APP_KEY={'a' * 40}\nLS_APP_SECRET={'b' * 40}\n",
        encoding="utf-8",
    )
    replacement.chmod(0o600)
    original_is_symlink = Path.is_symlink

    def swap_after_symlink_check(path: Path) -> bool:
        result = original_is_symlink(path)
        if path == secret:
            path.unlink()
            path.symlink_to(replacement)
        return result

    monkeypatch.setattr(Path, "is_symlink", swap_after_symlink_check)

    credentials = load_ls_credentials(secret)

    assert credentials.app_key == APP_KEY
    assert credentials.app_secret == APP_SECRET


def test_ls_secret_rejects_non_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "ls.env"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(LsSecretFileError):
        _ = load_ls_credentials(fifo)


def test_ls_secret_rejects_file_not_owned_by_current_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _secret(
        tmp_path,
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
    )
    current_uid = os.getuid()
    monkeypatch.setattr(ls_config.os, "getuid", lambda: current_uid + 1)

    with pytest.raises(LsSecretFileError):
        _ = load_ls_credentials(secret)


@pytest.mark.parametrize(
    "contents",
    (
        "",
        f"LS_APP_KEY={APP_KEY}\n",
        f"LS_APP_SECRET={APP_SECRET}\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\nOTHER=value\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={APP_SECRET}\nLS_APP_SECRET={APP_SECRET}\n",
        f"LS_APP_KEY=short\nLS_APP_SECRET={APP_SECRET}\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={'s' * 257}\n",
        f"LS_APP_KEY={APP_KEY} \nLS_APP_SECRET={APP_SECRET}\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET=s{' ' * 38}s\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={'s' * 39}\x7f\n",
        f"LS_APP_KEY={APP_KEY}\nLS_APP_SECRET={'s' * 39}한\n",
    ),
)
def test_ls_secret_requires_two_exact_bounded_ascii_settings(
    tmp_path: Path,
    contents: str,
) -> None:
    secret = _secret(tmp_path, contents)

    with pytest.raises(InvalidLsCredentialsError) as captured:
        _ = load_ls_credentials(secret)

    rendered = str(captured.value)
    assert APP_KEY not in rendered
    assert APP_SECRET not in rendered


def test_ls_credentials_reject_invalid_direct_construction() -> None:
    with pytest.raises(InvalidLsCredentialsError):
        _ = LsCredentials("short", APP_SECRET)


def test_ls_secret_rejects_invalid_utf8_without_rendering_bytes(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "ls.env"
    secret.write_bytes(
        b"LS_APP_KEY=" + b"k" * 40 + b"\nLS_APP_SECRET=" + b"s" * 39 + b"\xff\n"
    )
    secret.chmod(0o600)

    with pytest.raises(LsSecretEncodingError) as captured:
        _ = load_ls_credentials(secret)

    assert "xff" not in str(captured.value)


def test_production_ls_http_client_is_exact_and_does_not_follow_redirects() -> None:
    with create_ls_http_client() as client:
        assert LS_REST_BASE_URL == "https://openapi.ls-sec.co.kr:8080"
        assert str(client.base_url).rstrip("/") == LS_REST_BASE_URL
        assert client.follow_redirects is False
        assert client.trust_env is False


def _secret(
    tmp_path: Path,
    contents: str,
    *,
    mode: int = 0o600,
) -> Path:
    path = tmp_path / "ls.env"
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return path
