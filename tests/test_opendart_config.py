from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.opendart_config import (
    DEFAULT_OPENDART_SECRET_PATH,
    InvalidOpenDartCredentialsError,
    OpenDartSecretEncodingError,
    OpenDartSecretFileError,
    create_opendart_http_client,
    load_opendart_credentials,
)

API_KEY = "a" * 40


def test_opendart_config_uses_separate_mode_600_secret() -> None:
    expected = Path.home() / ".config/trading-agent/opendart.env"

    assert expected == DEFAULT_OPENDART_SECRET_PATH


def test_opendart_credentials_are_private_and_exact(tmp_path: Path) -> None:
    secret = _secret(tmp_path, f"OPENDART_API_KEY={API_KEY}\n")

    credentials = load_opendart_credentials(secret)

    assert credentials.api_key == API_KEY
    assert API_KEY not in repr(credentials)


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644, 0o700))
def test_opendart_secret_rejects_every_mode_except_600(
    tmp_path: Path,
    mode: int,
) -> None:
    secret = _secret(tmp_path, f"OPENDART_API_KEY={API_KEY}\n", mode=mode)

    with pytest.raises(OpenDartSecretFileError):
        _ = load_opendart_credentials(secret)


def test_opendart_secret_rejects_symlink(tmp_path: Path) -> None:
    target = _secret(tmp_path, f"OPENDART_API_KEY={API_KEY}\n")
    link = tmp_path / "opendart-link.env"
    link.symlink_to(target)

    with pytest.raises(OpenDartSecretFileError):
        _ = load_opendart_credentials(link)


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "OPENDART_API_KEY=short\n",
        f"OPENDART_API_KEY={API_KEY} \n",
        f"OTHER={API_KEY}\n",
        f"OPENDART_API_KEY={API_KEY}\nOTHER=value\n",
        f"OPENDART_API_KEY={API_KEY}\nOPENDART_API_KEY={API_KEY}\n",
    ),
)
def test_opendart_secret_requires_one_exact_setting(
    tmp_path: Path,
    contents: str,
) -> None:
    secret = _secret(tmp_path, contents)

    with pytest.raises(InvalidOpenDartCredentialsError) as captured:
        _ = load_opendart_credentials(secret)

    assert API_KEY not in str(captured.value)


def test_opendart_secret_rejects_invalid_utf8_without_rendering_bytes(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "opendart.env"
    secret.write_bytes(b"OPENDART_API_KEY=" + b"a" * 39 + b"\xff\n")
    secret.chmod(0o600)

    with pytest.raises(OpenDartSecretEncodingError) as captured:
        _ = load_opendart_credentials(secret)

    assert "xff" not in str(captured.value)


def test_production_http_client_is_exact_and_does_not_follow_redirects() -> None:
    with create_opendart_http_client() as client:
        assert str(client.base_url).rstrip("/") == "https://opendart.fss.or.kr"
        assert client.follow_redirects is False


def _secret(
    tmp_path: Path,
    contents: str,
    *,
    mode: int = 0o600,
) -> Path:
    path = tmp_path / "opendart.env"
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return path
