from __future__ import annotations

import stat
from pathlib import Path

import httpx2

import run_us_runtime_fleet_cycle as cli
from tests.alpaca_sip_runtime_fleet_fixtures import wire_bars
from tests.test_run_us_runtime_fleet_cycle import NOW, _arguments, _inputs
from trading_agent.us_news_catalyst_feature_artifact import feature_artifacts_in


def test_runtime_cycle_exports_private_news_catalyst_feature(tmp_path: Path) -> None:
    scanner, profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    feature_root = tmp_path / "news-features"

    def client_factory() -> httpx2.Client:
        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    200,
                    json={
                        "bars": {"FIXT": wire_bars("FIXT", 35)},
                        "next_page_token": None,
                    },
                )
            ),
            follow_redirects=False,
        )

    report_root = tmp_path / "report"
    argv = [
        *_arguments(
            tmp_path,
            profile,
            report_root,
            scanner=scanner,
            secret=secret,
        ),
        "--news-catalyst-feature-root",
        str(feature_root),
    ]

    assert cli.main(argv, now=NOW, client_factory=client_factory) == 0

    artifacts = feature_artifacts_in(feature_root)
    assert len(artifacts) == 1
    assert artifacts[0].payload.symbol == "FIXT"
    path = next(feature_root.glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    report = (report_root / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "news catalyst feature artifact: 1 new, 0 replay" in report
    assert "account/order mutation: 0" in report
