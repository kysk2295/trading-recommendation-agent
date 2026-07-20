from __future__ import annotations

import datetime as dt
import stat
import subprocess
from pathlib import Path

import httpx2

from run_us_news_catalyst_cohort_collect import (
    REPORT_NAME,
    UsNewsCatalystCollectionCliDependencies,
    main,
)
from tests.test_us_news_catalyst_cohort_collection import (
    _bars_response,
    _security_master,
)
from tests.test_us_news_catalyst_feature_observations import SETUP_AT, _cohort
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_security_master_models import (
    build_alpaca_security_master_snapshot,
)
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.us_news_catalyst_trial_artifact import publish_us_news_catalyst_cohort

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_us_news_catalyst_cohort_collect.py"


def test_cohort_collect_cli_help_is_executable() -> None:
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT), "--help"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--security-master-store" in result.stdout
    assert "--receipt-root" in result.stdout


def test_cohort_collect_cli_collects_and_replays_without_credentials(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []
    credential_reads = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _bars_response(request)

    def client_factory() -> httpx2.Client:
        return httpx2.Client(
            base_url=ALPACA_DATA_URL,
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    def credentials_loader(_path: Path) -> AlpacaCredentials:
        nonlocal credential_reads
        credential_reads += 1
        return AlpacaCredentials("fixture-key", "fixture-secret")

    cohort_path = _cohort_path(tmp_path)
    store_path = _security_store(tmp_path)
    argv = _argv(tmp_path, cohort_path, store_path)
    dependencies = UsNewsCatalystCollectionCliDependencies(
        clock=lambda: SETUP_AT,
        client_factory=client_factory,
        credentials_loader=credentials_loader,
    )

    assert main(argv, dependencies=dependencies) == 0
    first_count = len(requests)
    assert main(argv, dependencies=dependencies) == 0

    assert first_count == 84
    assert len(requests) == first_count
    assert credential_reads == 1
    receipts = tuple((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.is_file()
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    report = (tmp_path / "report" / REPORT_NAME).read_text(encoding="utf-8")
    assert "result: ready" in report
    assert "collection receipt: replay" in report
    assert "account/order mutation: 0" in report


def test_cohort_collect_cli_bad_input_is_redacted_and_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "secret-looking-cohort-name.json"
    dependencies = UsNewsCatalystCollectionCliDependencies(
        clock=lambda: SETUP_AT,
        client_factory=lambda: httpx2.Client(base_url=ALPACA_DATA_URL),
        credentials_loader=lambda _path: AlpacaCredentials("fixture", "fixture"),
    )

    code = main(
        _argv(tmp_path, missing, tmp_path / "missing-security.sqlite3"),
        dependencies=dependencies,
    )

    assert code == 1
    report = (tmp_path / "report" / REPORT_NAME).read_text(encoding="utf-8")
    assert "result: blocked" in report
    assert "collection receipt: not-published" in report
    assert missing.name not in report
    assert "account/order mutation: 0" in report


def _cohort_path(tmp_path: Path) -> Path:
    path, _created = publish_us_news_catalyst_cohort(
        tmp_path / "cohort",
        _cohort(tmp_path),
    )
    return path


def _security_store(tmp_path: Path) -> Path:
    path = tmp_path / "security-master.sqlite3"
    store = AlpacaSecurityMasterStore(path)
    raw = store.append_raw(
        dt.datetime(2026, 7, 21, 13, 59, tzinfo=dt.UTC),
        b"fixture-security-master",
    )
    fixture = _security_master()
    snapshot = build_alpaca_security_master_snapshot(
        raw.receipt_id,
        fixture.observed_at,
        fixture.instruments,
        fixture.aliases,
    )
    store.append_snapshot(snapshot)
    return path


def _argv(tmp_path: Path, cohort_path: Path, security_store: Path) -> list[str]:
    return [
        "--cohort",
        str(cohort_path),
        "--security-master-store",
        str(security_store),
        "--plan-root",
        str(tmp_path / "plans"),
        "--profile-root",
        str(tmp_path / "profiles"),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--canonical-root",
        str(tmp_path / "canonical"),
        "--feature-root",
        str(tmp_path / "features"),
        "--receipt-root",
        str(tmp_path / "receipts"),
        "--output-dir",
        str(tmp_path / "report"),
    ]
