from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.alpaca_paper_config import (
    ALPACA_PAPER_TRADING_URL,
    DEFAULT_ALPACA_PAPER_SECRET_PATH,
    AlpacaPaperSecretEncodingError,
    AlpacaPaperSecretFileError,
    NonPaperTradingEndpointError,
    load_alpaca_paper_credentials,
    require_paper_trading_url,
)


def test_paper_config_uses_separate_secret_file() -> None:
    # Given
    expected = Path.home() / ".config/trading-agent/alpaca-paper.env"

    # When
    actual = DEFAULT_ALPACA_PAPER_SECRET_PATH

    # Then
    assert actual == expected


def test_paper_endpoint_accepts_only_canonical_url() -> None:
    # Given
    canonical_url = ALPACA_PAPER_TRADING_URL

    # When
    actual = require_paper_trading_url(canonical_url)

    # Then
    assert actual == canonical_url


@pytest.mark.parametrize(
    "url",
    (
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets/v2",
    ),
)
def test_paper_endpoint_rejects_every_noncanonical_url(url: str) -> None:
    # Given / When / Then
    with pytest.raises(NonPaperTradingEndpointError, match="paper 전용"):
        _ = require_paper_trading_url(url)


def test_paper_credentials_require_exact_mode_600(tmp_path: Path) -> None:
    # Given
    secret = tmp_path / "alpaca-paper.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)

    # When
    credentials = load_alpaca_paper_credentials(secret)

    # Then
    assert "test-key" not in repr(credentials)
    assert "test-secret" not in repr(credentials)


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644, 0o700))
def test_paper_credentials_reject_every_mode_except_600(
    tmp_path: Path,
    mode: int,
) -> None:
    # Given
    secret = tmp_path / "alpaca-paper.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(mode)

    # When / Then
    with pytest.raises(AlpacaPaperSecretFileError, match="정확히 600"):
        _ = load_alpaca_paper_credentials(secret)


def test_paper_credentials_reject_invalid_utf8_without_rendering_bytes(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "alpaca-paper.env"
    secret.write_bytes(b"APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=\xffsecret\n")
    secret.chmod(0o600)

    with pytest.raises(AlpacaPaperSecretEncodingError) as captured:
        _ = load_alpaca_paper_credentials(secret)

    rendered = str(captured.value)
    assert "test-key" not in rendered
    assert "secret" not in rendered
    assert "xff" not in rendered
