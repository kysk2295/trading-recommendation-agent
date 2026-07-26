# Dashboard operator pairing runbook

## Preconditions

- The `ai.trading-agent.dashboard-publisher` launchd job is running exactly once.
- The resident publisher has connected to the existing Railway `observatory` service.
- Use this procedure only after the release containing the resident-publisher signal control path is installed.

## Pair one browser

Run the following on the publisher host. It signals the resident process; it does not
start a second publisher and it does not print a ticket or operator credential.

```sh
launchctl kill -USR1 "gui/$(id -u)/ai.trading-agent.dashboard-publisher"
```

The resident publisher sends one authenticated `pairing_request` on its existing
WebSocket. Railway returns one short-lived, single-use path directly to that process,
which invokes `/usr/bin/open` with an argv array. The ticket remains in process and
browser memory only.

While a request is pending, repeated `SIGUSR1` signals are coalesced and emit no
additional request. There is no automatic retry. After the browser opens, verify
`GET /api/operator/session` is authenticated and use the operator-only read receipts
endpoint. A consumed or expired URL is expected to return `404`; do not retry it.

## Safe local dry-run fixture

Do not use `--dry-run --once publish`: it is rejected before credentials or network
access because global options do not configure the `publish` subcommand. The
deterministic no-network smoke is the controlled mode-600 fixture test:

```sh
.venv/bin/python -m pytest -q \
  tests/test_dashboard_publisher_cli.py::test_publisher_dry_run_cli_terminates_from_controlled_private_fixture
```

That test invokes the exact CLI form below with a temporary owner-private output root
and fixture credential file, asserts a five-second bound, and parses a redacted v2
snapshot. It does not open a WebSocket or send an external request.

```sh
.venv/bin/python run_dashboard_publisher.py publish \
  --outputs <owner-private-fixture-root> \
  --credentials <mode-600-fixture-credentials> \
  --dry-run --once
```
