from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.test_alpaca_sip_dynamic_quote_actionability import (
    _SCAN_STARTED_AT,
    _base,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifestError,
    build_alpaca_sip_quote_actionability_manifest,
    read_alpaca_sip_quote_actionability_manifest,
    write_alpaca_sip_quote_actionability_manifest,
)


def test_manifest_is_canonical_private_and_immutable(tmp_path: Path) -> None:
    manifest = _manifest()
    path = tmp_path / "manifest.json"

    assert write_alpaca_sip_quote_actionability_manifest(path, manifest) is True
    assert write_alpaca_sip_quote_actionability_manifest(path, manifest) is False
    assert read_alpaca_sip_quote_actionability_manifest(path) == manifest
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes().endswith(b"}")

    changed = build_alpaca_sip_quote_actionability_manifest(
        _base(entry="100.10", stop="98.90"),
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    with pytest.raises(AlpacaSipQuoteActionabilityManifestError):
        _ = write_alpaca_sip_quote_actionability_manifest(path, changed)


def test_manifest_reader_rejects_noncanonical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    assert write_alpaca_sip_quote_actionability_manifest(path, _manifest()) is True
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(AlpacaSipQuoteActionabilityManifestError):
        _ = read_alpaca_sip_quote_actionability_manifest(path)


def _manifest():
    return build_alpaca_sip_quote_actionability_manifest(
        _base(entry="100.10", stop="99.00"),
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
