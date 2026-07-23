from __future__ import annotations

import base64
import datetime as dt
import json
import stat
from pathlib import Path

import pytest
import typer

import run_issuer_announcement_collect as cli
from trading_agent.data_capability_models import DataSourceId, DataUse
from trading_agent.data_capability_registry import DataCapabilityRegistryStore
from trading_agent.issuer_announcement_models import (
    IssuerAnnouncementOnboarding,
    IssuerAnnouncementRequest,
    IssuerAnnouncementTerminal,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "issuer_announcement"


def test_fixture_collection_is_raw_first_capability_bound_and_provider_free_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onboarding = _onboarding(tmp_path / "input" / "onboarding.json")
    response = _response(tmp_path / "input" / "feed.xml")
    store = tmp_path / "store"
    registry = tmp_path / "registry" / "capabilities.sqlite3"
    output = tmp_path / "report"

    _run(onboarding, response, store, registry, output)
    first = _report(output)

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("exact replay must not open the provider")

    monkeypatch.setattr(cli, "fetch_issuer_announcement_feed", reject_network)
    _run(onboarding, tmp_path / "missing.xml", store, registry, output)
    second = _report(output)

    receipt_path = next(store.glob("*.receipt.json"))
    terminal_path = next(store.glob("*.terminal.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    terminal = terminal_path.read_text(encoding="utf-8")
    raw = base64.b64decode(receipt["raw_payload_base64"], validate=True)
    completed_at = IssuerAnnouncementTerminal.model_validate_json(terminal).completed_at
    snapshot = DataCapabilityRegistryStore(registry).snapshot(
        as_of=completed_at + dt.timedelta(seconds=1),
        source_ids=(
            DataSourceId(
                provider="issuer_direct",
                feed="example_investor_announcements",
            ),
        ),
    )

    assert raw == response.read_bytes()
    assert "Example issuer announces quarterly dividend" not in terminal
    assert "result: success" in first
    assert "announcement metadata: 1" in first
    assert "replayed: no" in first
    assert "network access: 0" in first
    assert "replayed: yes" in second
    assert "ABCD" not in first + second
    assert "Example issuer announces quarterly dividend" not in first + second
    assert "issuer.example" not in first + second
    assert str(tmp_path) not in first + second
    assert len(snapshot.capabilities) == 1
    assert len(snapshot.entitlements) == 1
    assert (
        snapshot.capabilities[0].source_id.canonical_id
        == "issuer_direct/example_investor_announcements"
    )
    assert snapshot.entitlements[0].permitted_uses == (
        DataUse.HISTORICAL_RESEARCH,
        DataUse.SHADOW_FORWARD,
    )
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(terminal_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_malformed_feed_preserves_raw_receipt_before_failed_terminal(tmp_path: Path) -> None:
    onboarding = _onboarding(tmp_path / "input" / "onboarding.json")
    response = _response(tmp_path / "input" / "feed.xml", payload=b"<rss><channel>")
    store = tmp_path / "store"

    with pytest.raises(typer.Exit) as error:
        _run(
            onboarding,
            response,
            store,
            tmp_path / "registry" / "capabilities.sqlite3",
            tmp_path / "report",
        )

    assert error.value.exit_code == 2
    receipt = json.loads(next(store.glob("*.receipt.json")).read_text(encoding="utf-8"))
    terminal = json.loads(next(store.glob("*.terminal.json")).read_text(encoding="utf-8"))
    assert base64.b64decode(receipt["raw_payload_base64"], validate=True) == b"<rss><channel>"
    assert terminal["status"] == "failed"
    assert terminal["failure_code"] == "response_structure"
    assert terminal["announcement_count"] == 0


def test_non_private_onboarding_blocks_before_fixture_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onboarding = _onboarding(tmp_path / "input" / "onboarding.json")
    onboarding.chmod(0o644)
    opened = False

    def reject_fixture(_path: Path) -> bytes:
        nonlocal opened
        opened = True
        raise AssertionError

    monkeypatch.setattr(cli, "read_private_bytes_query_only", reject_fixture)

    with pytest.raises(typer.BadParameter):
        _run(
            onboarding,
            tmp_path / "input" / "missing.xml",
            tmp_path / "store",
            tmp_path / "registry" / "capabilities.sqlite3",
            tmp_path / "report",
        )

    assert opened is False
    assert not (tmp_path / "store").exists()


def test_onboarding_rejects_missing_automation_rights_and_paper_use() -> None:
    payload = _onboarding_payload()
    payload["automated_access_permitted"] = False
    with pytest.raises(ValueError):
        IssuerAnnouncementOnboarding.model_validate(payload)

    payload = _onboarding_payload()
    source_id = payload["source_id"]
    assert isinstance(source_id, dict)
    source_id["provider"] = "licensed_news"
    with pytest.raises(ValueError):
        IssuerAnnouncementOnboarding.model_validate(payload)

    source = IssuerAnnouncementOnboarding.model_validate(_onboarding_payload())
    stale = source.model_copy(
        update={
            "license_reviewed_at": dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            "effective_from": dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        }
    )
    with pytest.raises(ValueError):
        IssuerAnnouncementRequest(
            collection_id="stale-license-review",
            onboarding=stale,
            requested_at=dt.datetime(2026, 7, 23, tzinfo=dt.UTC),
        )

    payload = _onboarding_payload()
    payload["permitted_uses"] = ["paper_recommendation"]
    with pytest.raises(ValueError):
        IssuerAnnouncementOnboarding.model_validate(payload)


def _run(
    onboarding: Path,
    response: Path,
    store: Path,
    registry: Path,
    output: Path,
) -> None:
    cli.main(
        collection_id="issuer-announcement-cli-001",
        requested_at="2026-07-23T20:15:00Z",
        onboarding=str(onboarding),
        fixture_response=str(response),
        store_dir=str(store),
        registry=str(registry),
        output_dir=str(output),
    )


def _onboarding(path: Path) -> Path:
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps(_onboarding_payload()), encoding="utf-8")
    path.chmod(0o600)
    return path


def _onboarding_payload() -> dict[str, object]:
    return json.loads(
        (FIXTURE_ROOT / "onboarding.json").read_text(encoding="utf-8")
    )


def _response(path: Path, *, payload: bytes | None = None) -> Path:
    path.write_bytes(payload or (FIXTURE_ROOT / "feed.xml").read_bytes())
    path.chmod(0o600)
    return path


def _report(output: Path) -> str:
    return (output / cli.REPORT_NAME).read_text(encoding="utf-8")
