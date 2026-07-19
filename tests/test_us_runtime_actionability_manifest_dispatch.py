from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.test_alpaca_sip_dynamic_quote_actionability import _base
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_runtime_actionability_manifest_dispatch import (
    UsRuntimeActionabilityManifestDispatchError,
    dispatch_us_runtime_actionability_manifests,
)


def test_dispatcher_writes_one_manifest_and_exact_replay(tmp_path: Path) -> None:
    publication = _base(entry="100.10", stop="99.00")
    bindings = (UsFeatureEvidenceBinding("AAA", quote_fixtures._snapshot()),)

    first = dispatch_us_runtime_actionability_manifests(
        (publication,),
        bindings,
        dynamic_fixtures._plan(),
        tmp_path / "manifests",
    )
    second = dispatch_us_runtime_actionability_manifests(
        (publication,),
        bindings,
        dynamic_fixtures._plan(),
        tmp_path / "manifests",
    )

    assert first.created_count == 1
    assert first.replay_count == 0
    assert second.created_count == 0
    assert second.replay_count == 1
    assert second.manifests == first.manifests
    assert first.manifests[0].scan_started_at == publication.signal.observed_at
    assert len(tuple((tmp_path / "manifests").glob("*.json"))) == 1


def test_dispatcher_with_no_current_signal_is_noop(tmp_path: Path) -> None:
    result = dispatch_us_runtime_actionability_manifests(
        (),
        (UsFeatureEvidenceBinding("AAA", quote_fixtures._snapshot()),),
        dynamic_fixtures._plan(),
        tmp_path / "manifests",
    )

    assert result.manifests == ()
    assert result.created_count == 0
    assert result.replay_count == 0
    assert not (tmp_path / "manifests").exists()


def test_dispatcher_rejects_ambiguous_current_signals_before_write(tmp_path: Path) -> None:
    first = _base(entry="100.10", stop="99.00")
    payload = first.model_dump(mode="json")
    payload["signal"]["signal_id"] = "second-current-signal"
    second = type(first).model_validate(payload)

    with pytest.raises(UsRuntimeActionabilityManifestDispatchError):
        _ = dispatch_us_runtime_actionability_manifests(
            (first, second),
            (UsFeatureEvidenceBinding("AAA", quote_fixtures._snapshot()),),
            dynamic_fixtures._plan(),
            tmp_path / "manifests",
        )

    assert not (tmp_path / "manifests").exists()


def test_dispatcher_rejects_binding_not_owned_by_plan(tmp_path: Path) -> None:
    with pytest.raises(UsRuntimeActionabilityManifestDispatchError):
        _ = dispatch_us_runtime_actionability_manifests(
            (),
            (UsFeatureEvidenceBinding("BBB", quote_fixtures._snapshot()),),
            dynamic_fixtures._plan(),
            tmp_path / "manifests",
        )

    assert not (tmp_path / "manifests").exists()
