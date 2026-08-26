# Local Agent Browser Computer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the persistent Autonomous Trading Supervisor a launchd-owned, restart-safe local Chrome computer that can autonomously choose read-only web research actions, preserve browser and social-evidence lineage, and continue a durable Korean-market research agenda without depending on Codex, chat sessions, or an open terminal.

**Architecture:** A standalone Local Browser Gateway owns one dedicated Chrome profile and controls Chrome through the DevTools Protocol (CDP). The gateway exposes a bounded canonical-JSON protocol over a current-user-only Unix socket; the Supervisor receives role-scoped browser tools that call this gateway from its existing isolated tool workers. Browser observations are copied into a separate append-only social-evidence store, while a durable agenda wrapper keeps exactly one continuing `market_context` / `kr_equities` research episode alive and creates a lineage-linked successor after a terminal episode. Deterministic code enforces URL, filesystem, process, payload, and audit boundaries only; the LLM remains free to choose sites, tool order, repetition, hypotheses, and when to wait.

**Tech Stack:** Python 3.12, Pydantic v2 frozen models, SQLite append-only private stores, Unix domain sockets, Google Chrome, Chrome DevTools Protocol, `httpx`, `websockets` 16 sync client, existing Autonomous Trading Supervisor, anyio foreground service, launchd, pytest, Ruff, basedpyright

---

## Scope and non-goals

This is implementation subproject **12.1 Local Agent Browser Computer** from the approved KR browser-research design. It ends when the production Research Agent can autonomously use and recover the local browser computer and preserve the resulting evidence. It deliberately does **not** yet:

- create KR recommendations, entries, stops, targets, virtual fills, or position management;
- add Loop Engineer code modification or promotion;
- submit Alpaca Paper orders or call any KIS/LS account, balance, position, or order endpoint;
- automate login credentials, CAPTCHA bypass, posting, comments, likes, purchases, downloads, or any browser-side mutation;
- hardcode a `browser -> KIS -> critic` workflow or require a fixed number of pages;
- change or reuse `trading_agent/social_evidence_models.py`, whose official-API-only contract remains intact.

This subproject makes no broker network request at all. The existing Alpaca boundary remains unchanged: if a later subproject performs a trading call, only the exact Paper base URL `https://paper-api.alpaca.markets` is admissible and every other trading URL must be rejected before HTTP.

The dedicated Chrome profile may be logged into X or Grok manually once by the operator. Cookies, tokens, request headers, raw authentication responses, account identifiers, full HTML, and full page bodies must never enter the project, gateway receipts, evidence database, logs, or CLI output.

## Fixed production paths and labels

- Gateway config: `/Users/goyunseo/.config/trading-agent/local-browser-gateway-v1.json`
- Gateway plist: `/Users/goyunseo/Library/LaunchAgents/ai.trading-agent.local-browser-gateway-v1.plist`
- launchd label: `ai.trading-agent.local-browser-gateway`
- Gateway state root: `/Users/goyunseo/.local/state/trading-agent/local-browser-gateway-v1`
- Dedicated Chrome profile: `/Users/goyunseo/.local/state/trading-agent/local-browser-profile-v1`
- Unix socket: `/Users/goyunseo/.local/state/trading-agent/local-browser-gateway-v1/browser.sock`
- Receipt database: `/Users/goyunseo/.local/state/trading-agent/local-browser-gateway-v1/receipts.sqlite3`
- Screenshot root: `/Users/goyunseo/.local/state/trading-agent/local-browser-gateway-v1/screenshots`
- Chrome executable: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`

All private directories are current-user-owned mode `700`; immutable config and plist files, SQLite files, screenshots, and cached port metadata are mode `600`; symlinks are rejected. The Unix socket lives under the private state root, is mode `600`, and rejects peers whose effective UID differs from the gateway process UID.

## Tool surface and autonomy boundary

The Supervisor receives these seven browser tools in addition to the existing `evidence.read`, `memory.search`, and `task.history` tools, keeping the reasoning request at 10 tools, below its limit of 16:

| Tool | Purpose | Roles |
|---|---|---|
| `browser.status` | Inspect gateway/Chrome readiness and active page count | Market Observer, Research, Critic |
| `browser.search` | Open an HTTPS Google search chosen by the agent | Market Observer, Research |
| `browser.open` | Open an agent-selected public HTTPS URL | Market Observer, Research |
| `browser.read` | Read bounded visible text and links and persist one evidence record | Market Observer, Research, Critic |
| `browser.follow` | Follow a bounded link index from the current page | Market Observer, Research |
| `browser.capture` | Save a private screenshot and return only its digest/receipt | Market Observer, Research, Critic |
| `social.evidence.search` | Search prior browser evidence without opening Chrome | Market Observer, Opportunity, Research, Critic |

There is no fixed action sequence. Tool bindings constrain permissions and payload size, not research strategy. A visual-only or bot-blocked page returns a durable blocked observation when no reviewed Computer Use adapter is configured; it must not silently invent content or bypass the site.

## File structure

### New production files

- `trading_agent/local_browser_protocol.py`: strict request/response models, action names, bounded page observations, URL policy.
- `trading_agent/local_browser_gateway_config.py`: immutable gateway config, private paths, launchd plist, verification.
- `trading_agent/local_chrome_controller.py`: dedicated Chrome lifecycle, `DevToolsActivePort`, restart/reconnect.
- `trading_agent/chrome_devtools_client.py`: bounded CDP navigation, visible text/link extraction, screenshot capture.
- `trading_agent/local_browser_gateway.py`: private Unix-socket server/client, peer check, dispatch, append-only receipts.
- `trading_agent/browser_social_evidence.py`: browser-captured social/news/community evidence contracts.
- `trading_agent/browser_social_evidence_store.py`: append-only searchable evidence authority.
- `trading_agent/autonomous_browser_tools.py`: role-scoped Supervisor bindings backed by the gateway and evidence store.
- `trading_agent/browser_research_agenda.py`: durable episodes, predecessor lineage, continuous Supervisor wrapper.
- `run_local_browser_gateway.py`: `provision`, `verify`, `run`, `status`, and `activate` CLI.

### Existing production files to modify

- `trading_agent/autonomous_supervisor_service.py`: compose foundation and browser bindings without changing the multi-step reasoning loop.
- `trading_agent/research_agent_service_config.py`: backward-compatible schema v3 gateway reference while retaining byte-exact schema v2 replay.
- `trading_agent/research_agent_service_cli_args.py`: optional `--browser-gateway-config` provision argument.
- `trading_agent/research_agent_service_builder.py`: install browser tools and the continuous KR agenda only for schema v3.
- `run_research_agent_runtime.py`: retain existing CLI behavior while accepting the schema v3 provision input.

### New and modified tests

- Create `tests/test_local_browser_protocol.py`
- Create `tests/test_local_browser_gateway_config.py`
- Create `tests/test_local_chrome_controller.py`
- Create `tests/test_chrome_devtools_client.py`
- Create `tests/test_local_browser_gateway.py`
- Create `tests/test_browser_social_evidence_store.py`
- Create `tests/test_autonomous_browser_tools.py`
- Create `tests/test_browser_research_agenda.py`
- Create `tests/test_local_browser_gateway_cli.py`
- Modify `tests/test_autonomous_supervisor_service.py`
- Modify `tests/test_research_agent_service_cli.py`
- Modify `tests/test_research_agent_service_runtime.py`

---

## Task 1: Define the read-only browser protocol and URL boundary

**Files:**
- Create: `trading_agent/local_browser_protocol.py`
- Create: `tests/test_local_browser_protocol.py`

- [ ] **Step 1: Write failing protocol and hostile-URL tests**

```python
@pytest.mark.parametrize(
    "url",
    (
        "http://example.com",
        "https://user:password@example.com/private",
        "https://127.0.0.1/admin",
        "https://[::1]/admin",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,secret",
        "chrome://settings",
    ),
)
def test_navigation_rejects_non_public_https_urls(url: str) -> None:
    with pytest.raises(InvalidLocalBrowserProtocolError):
        BrowserOpenRequest(request_id="a" * 64, url=url)


def test_page_observation_is_bounded_and_does_not_accept_raw_html() -> None:
    with pytest.raises(ValidationError):
        BrowserPageObservation.model_validate(
            {
                "target_id": "target-1",
                "url": "https://example.com/story",
                "title": "Story",
                "visible_text": "bounded",
                "links": [],
                "raw_html": "<html>forbidden</html>",
            }
        )
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `uv run pytest -q tests/test_local_browser_protocol.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.local_browser_protocol`.

- [ ] **Step 3: Implement strict canonical request and response models**

Define `BrowserAction` as exactly `status`, `search`, `open`, `read`, `follow`, and `capture`. Define frozen/strict Pydantic models with:

- 64-character lowercase request IDs;
- query length 1–500;
- target ID length 1–256;
- link index 0–99;
- visible text maximum 12,000 characters;
- at most 40 links with label maximum 200 and URL maximum 2,048;
- response JSON maximum 16 KiB;
- stable failure reasons rather than exception strings.

```python
class BrowserPageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    target_id: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=8, max_length=2_048)
    title: str = Field(default="", max_length=500)
    visible_text: str = Field(default="", max_length=12_000)
    links: tuple[BrowserVisibleLink, ...] = Field(default=(), max_length=40)
    captured_at: AwareDatetime
```

- [ ] **Step 4: Implement fail-closed public HTTPS validation**

Use `urllib.parse.urlsplit`, reject username/password, fragments containing credentials, literal loopback/link-local/private/reserved/multicast IPs through `ipaddress.ip_address`, non-HTTPS schemes, missing/invalid host names, non-default ports, and all browser/internal schemes. Return a normalized URL with lowercase host and no fragment. Do not perform DNS resolution in this first boundary because DNS rebinding is handled by Chrome’s isolated read-only profile and no local HTTP schemes/ports are accepted.

```python
def require_public_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        raise InvalidLocalBrowserProtocolError(reason="browser_url_not_public_https")
    if parsed.hostname is None or parsed.port not in (None, 443):
        raise InvalidLocalBrowserProtocolError(reason="browser_url_not_public_https")
    _reject_nonpublic_literal(parsed.hostname)
    normalized = parsed._replace(netloc=parsed.hostname.lower(), fragment="")
    return urlunsplit(normalized)
```

- [ ] **Step 5: Pass the protocol tests**

Run: `uv run pytest -q tests/test_local_browser_protocol.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add trading_agent/local_browser_protocol.py tests/test_local_browser_protocol.py
git commit -m "feat: define read-only local browser protocol"
```

## Task 2: Define the private gateway config and launchd contract

**Files:**
- Create: `trading_agent/local_browser_gateway_config.py`
- Create: `tests/test_local_browser_gateway_config.py`

- [ ] **Step 1: Write failing canonical config, permission, and plist tests**

```python
def test_gateway_config_round_trips_canonically_and_plist_is_keepalive(tmp_path: Path) -> None:
    config = gateway_config_fixture(tmp_path)
    config_path = tmp_path / "gateway.json"
    plist_path = tmp_path / "gateway.plist"
    assert write_local_browser_gateway_config(config_path, config)
    assert write_local_browser_launch_agent(plist_path, config, config_path)
    assert load_local_browser_gateway_config(config_path) == config
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == "ai.trading-agent.local-browser-gateway"
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["Umask"] == 0o077


def test_gateway_config_rejects_symlink_and_group_readable_file(tmp_path: Path) -> None:
    config_path = private_gateway_config_fixture(tmp_path)
    config_path.chmod(0o640)
    with pytest.raises(InvalidLocalBrowserGatewayConfigError):
        load_local_browser_gateway_config(config_path)
```

- [ ] **Step 2: Run and confirm the missing-module failure**

Run: `uv run pytest -q tests/test_local_browser_gateway_config.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.local_browser_gateway_config`.

- [ ] **Step 3: Implement schema v1 config and cross-field invariants**

```python
class LocalBrowserGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    label: Literal["ai.trading-agent.local-browser-gateway"] = LOCAL_BROWSER_GATEWAY_LABEL
    project_root: Path
    uv_path: Path
    chrome_executable: Path
    state_root: Path
    profile_root: Path
    socket_path: Path
    receipt_database: Path
    screenshot_root: Path
    startup_timeout_seconds: float = Field(default=30.0, ge=1.0, le=60.0)
    command_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
```

Require absolute paths, the socket/receipt/screenshot paths beneath `state_root`, `profile_root` outside the project, and exact label. Reuse `publish_private_immutable_text` and `read_private_text`; never write secrets into the config.

- [ ] **Step 4: Implement the launchd plist and verification**

The generated command must be exactly the configured `uv_path`, `run`, `--offline`, `python`, `<project>/run_local_browser_gateway.py`, `run`, `--config`, `<config>`. Require Chrome, `uv`, project root, and gateway script to exist; require Chrome and `uv` to be executable.

```python
payload = {
    "Label": config.label,
    "ProgramArguments": arguments,
    "KeepAlive": True,
    "RunAtLoad": True,
    "ProcessType": "Background",
    "ThrottleInterval": 30,
    "Umask": 0o077,
    "StandardOutPath": "/dev/null",
    "StandardErrorPath": "/dev/null",
}
```

- [ ] **Step 5: Pass config tests**

Run: `uv run pytest -q tests/test_local_browser_gateway_config.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add trading_agent/local_browser_gateway_config.py tests/test_local_browser_gateway_config.py
git commit -m "feat: define local browser gateway service contract"
```

## Task 3: Own and recover the dedicated Chrome process

**Files:**
- Create: `trading_agent/local_chrome_controller.py`
- Create: `tests/test_local_chrome_controller.py`

- [ ] **Step 1: Write failing lifecycle and recovery tests with a fake process launcher**

```python
def test_start_uses_dedicated_profile_and_loopback_ephemeral_debugging(tmp_path: Path) -> None:
    launcher = RecordingChromeLauncher(write_devtools_port="43123\n/devtools/browser/browser-1\n")
    controller = LocalChromeController(chrome_config_fixture(tmp_path), launcher=launcher)
    endpoint = controller.ensure_ready()
    assert "--remote-debugging-address=127.0.0.1" in launcher.command
    assert "--remote-debugging-port=0" in launcher.command
    assert f"--user-data-dir={controller.config.profile_root}" in launcher.command
    assert endpoint.port == 43123


def test_dead_owned_chrome_is_restarted_once_and_reconnected(tmp_path: Path) -> None:
    launcher = RestartingChromeLauncher(tmp_path)
    controller = LocalChromeController(chrome_config_fixture(tmp_path), launcher=launcher)
    first = controller.ensure_ready()
    launcher.kill(first.process_id)
    second = controller.ensure_ready()
    assert launcher.starts == 2
    assert second.process_id != first.process_id
```

- [ ] **Step 2: Run and confirm the missing-module failure**

Run: `uv run pytest -q tests/test_local_chrome_controller.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.local_chrome_controller`.

- [ ] **Step 3: Implement private directory preparation and Chrome launch**

Launch only the configured executable with:

```python
command = (
    str(config.chrome_executable),
    f"--user-data-dir={config.profile_root}",
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
)
```

Use `subprocess.Popen` with stdin/stdout/stderr to `DEVNULL`, `start_new_session=True`, and no shell. Wait boundedly for `<profile_root>/DevToolsActivePort`; require exactly a decimal loopback port and a `/devtools/browser/` path. Do not log process environment or command-line user data.

- [ ] **Step 4: Implement reconnect and shutdown semantics**

`ensure_ready()` reuses a healthy owned process, reconnects to an already-running dedicated profile only when the port file and `/json/version` are healthy, and starts a new process otherwise. `close()` terminates only the PID this gateway started, waits five seconds, then kills only that exact still-owned PID. Never use `pkill`, process-name matching, or broad filesystem deletion.

- [ ] **Step 5: Pass lifecycle tests**

Run: `uv run pytest -q tests/test_local_chrome_controller.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add trading_agent/local_chrome_controller.py tests/test_local_chrome_controller.py
git commit -m "feat: manage restart-safe dedicated Chrome"
```

## Task 4: Implement bounded Chrome DevTools operations

**Files:**
- Create: `trading_agent/chrome_devtools_client.py`
- Create: `tests/test_chrome_devtools_client.py`

- [ ] **Step 1: Write failing CDP request/response tests against a fake endpoint**

```python
def test_read_returns_only_bounded_visible_text_and_https_links() -> None:
    transport = FixtureCdpTransport(page_fixture_with_hidden_text_and_links())
    client = ChromeDevToolsClient(transport)
    observation = client.read("target-1", captured_at=NOW)
    assert "hidden-secret" not in observation.visible_text
    assert len(observation.visible_text) <= 12_000
    assert all(link.url.startswith("https://") for link in observation.links)
    assert len(observation.links) <= 40


def test_capture_writes_private_png_and_returns_digest_not_bytes(tmp_path: Path) -> None:
    client = ChromeDevToolsClient(FixtureCdpTransport(screenshot_png_fixture()))
    receipt = client.capture("target-1", tmp_path, captured_at=NOW)
    assert receipt.path.stat().st_mode & 0o777 == 0o600
    assert receipt.sha256 == hashlib.sha256(receipt.path.read_bytes()).hexdigest()
```

- [ ] **Step 2: Run and confirm the missing-module failure**

Run: `uv run pytest -q tests/test_chrome_devtools_client.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.chrome_devtools_client`.

- [ ] **Step 3: Implement target discovery and one-command-at-a-time CDP transport**

Use `httpx.Client(trust_env=False)` only against `http://127.0.0.1:<validated-port>` for `/json/version`, `/json/list`, and `/json/new`. Connect to the returned loopback WebSocket with `websockets.sync.client.connect`, send monotonically increasing integer IDs, require matching response IDs, and cap incoming messages at 1 MiB and the configured command timeout.

- [ ] **Step 4: Implement status, search, open, read, follow, and capture**

- `search(query)` URL-encodes the query into `https://www.google.com/search?q=<query>` and delegates to `open`.
- `open(url)` revalidates the public HTTPS URL, creates/navigates a target, waits for `document.readyState` to become `interactive` or `complete` within the command timeout, and returns target ID, final URL, and title.
- `read(target_id)` uses a constant JavaScript expression, never interpolated page/user text, to return `document.title`, `location.href`, `document.body.innerText`, and visible anchors. It truncates deterministically and excludes blank/non-HTTPS links.
- `follow(target_id, link_index)` obtains the current bounded link list, validates the selected URL again, then navigates the same target.
- `capture(target_id)` uses `Page.captureScreenshot` with PNG, decodes under an 8 MiB limit, writes mode `600`, and returns only path, digest, dimensions when present, and capture time.

- [ ] **Step 5: Fail honestly on visual-only or blocked pages**

Return stable reasons `browser_visible_text_unavailable`, `browser_navigation_blocked`, or `browser_cdp_timeout`. Do not OCR screenshots, invoke a hidden LLM, solve CAPTCHA, or claim an observation when the DOM result is empty.

- [ ] **Step 6: Pass CDP tests**

Run: `uv run pytest -q tests/test_chrome_devtools_client.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add trading_agent/chrome_devtools_client.py tests/test_chrome_devtools_client.py
git commit -m "feat: add bounded Chrome DevTools operations"
```

## Task 5: Expose the gateway through a private Unix socket and audit receipts

**Files:**
- Create: `trading_agent/local_browser_gateway.py`
- Create: `tests/test_local_browser_gateway.py`

- [ ] **Step 1: Write failing peer, replay, and receipt tests**

```python
def test_gateway_rejects_a_different_peer_uid_before_dispatch(tmp_path: Path) -> None:
    gateway = gateway_fixture(tmp_path, peer_uid=lambda _socket: os.geteuid() + 1)
    response = gateway.handle_bytes(status_request_bytes())
    assert response.reason == "browser_peer_uid_rejected"
    assert gateway.dispatch_count == 0


def test_exact_request_replay_is_idempotent_but_changed_payload_conflicts(tmp_path: Path) -> None:
    gateway = gateway_fixture(tmp_path)
    request = open_request_fixture(request_id="a" * 64, url="https://example.com")
    first = gateway.handle(request)
    assert gateway.handle(request) == first
    changed = request.model_copy(update={"url": "https://example.org"})
    with pytest.raises(InvalidLocalBrowserGatewayError):
        gateway.handle(changed)
```

Also assert receipt DB mode `600`, SQL update/delete triggers, canonical response replay after restart, request/response size limits, socket mode `600`, and socket cleanup only when it is a socket owned by the current user.

- [ ] **Step 2: Run and confirm the missing-module failure**

Run: `uv run pytest -q tests/test_local_browser_gateway.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.local_browser_gateway`.

- [ ] **Step 3: Implement the append-only receipt store**

Store `request_id`, action, canonical request SHA-256, canonical bounded response JSON, response SHA-256, status, stable reason, target ID, normalized URL, observation/screenshot digest, and occurred time. Never store headers, cookies, tokens, full HTML, full response bodies, or authentication data. Add `BEFORE UPDATE` and `BEFORE DELETE` triggers to both request and response tables.

- [ ] **Step 4: Implement one-request-per-connection Unix socket server/client**

Use a maximum 16 KiB newline-delimited canonical JSON request and response. Before reading a request, call an injected peer credential function whose macOS production implementation uses `socket.getpeereid()` and requires both peer UID and gateway UID to equal `os.geteuid()`. Fail closed when peer credentials are unavailable. Set the socket to mode `600` immediately after bind.

```python
def _require_current_user_peer(connection: socket.socket) -> None:
    peer_uid, _peer_gid = connection.getpeereid()
    if peer_uid != os.geteuid():
        raise InvalidLocalBrowserGatewayError(reason="browser_peer_uid_rejected")
```

The synchronous client connects with a bounded timeout, sends one canonical request, half-closes writing, reads one bounded response, and validates the matching request ID.

- [ ] **Step 5: Dispatch only the six protocol actions**

Dispatch to `LocalChromeController` and `ChromeDevToolsClient`. Every exception must become a stable, redacted error response and receipt. A repeated identical request ID returns the recorded response without touching Chrome; the same ID with a different payload fails before dispatch.

- [ ] **Step 6: Pass gateway tests**

Run: `uv run pytest -q tests/test_local_browser_gateway.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add trading_agent/local_browser_gateway.py tests/test_local_browser_gateway.py
git commit -m "feat: serve audited local browser gateway"
```

## Task 6: Persist browser-captured social evidence separately

**Files:**
- Create: `trading_agent/browser_social_evidence.py`
- Create: `trading_agent/browser_social_evidence_store.py`
- Create: `tests/test_browser_social_evidence_store.py`

- [ ] **Step 1: Write failing lineage, append-only, and search tests**

```python
def test_browser_observation_preserves_source_and_capture_lineage(tmp_path: Path) -> None:
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    evidence = browser_social_evidence_fixture()
    assert store.append(evidence)
    persisted = store.get(evidence.evidence_id)
    assert persisted is not None
    assert persisted.browser_receipt_id == evidence.browser_receipt_id
    assert persisted.first_observed_at <= persisted.captured_at
    assert persisted.content_sha256 == hashlib.sha256(persisted.excerpt.encode()).hexdigest()


def test_search_returns_bounded_records_without_raw_page_content(tmp_path: Path) -> None:
    store = seeded_browser_social_store(tmp_path)
    results = store.search("semiconductor", limit=5)
    assert len(results) <= 5
    assert all(len(item.excerpt) <= 2_000 for item in results)
```

Also test exact replay, changed-payload conflict, SQL update/delete rejection, mode `600`, symlink rejection, current-user ownership, optional `published_at`, and deterministic provisional cluster IDs.

- [ ] **Step 2: Run and confirm missing modules**

Run: `uv run pytest -q tests/test_browser_social_evidence_store.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.browser_social_evidence`.

- [ ] **Step 3: Implement browser evidence contracts**

```python
class BrowserSocialEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    browser_receipt_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_url: str = Field(min_length=8, max_length=2_048)
    source_kind: Literal["social", "community", "news", "search", "web"]
    source_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(default="", max_length=500)
    author_label: str = Field(default="", max_length=200)
    excerpt: str = Field(min_length=1, max_length=2_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    repost_cluster_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    independent_source_cluster_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_at: AwareDatetime | None = None
    first_observed_at: AwareDatetime
    captured_at: AwareDatetime
    screenshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
```

For this release only, set `repost_cluster_id = content_sha256` and `independent_source_cluster_id = source_identity_sha256`. Task 12.3 may append normalization analysis events later; it must not rewrite these original records.

- [ ] **Step 4: Implement the append-only SQLite store and bounded search**

Use canonical JSON plus SHA-256, exact-replay idempotency, changed-payload conflict, and update/delete triggers. Search title, author label, excerpt, and normalized URL with escaped `LIKE` terms; order by `captured_at DESC, evidence_id`; limit 1–20. The store is separate from the official API social-evidence store.

- [ ] **Step 5: Pass evidence tests**

Run: `uv run pytest -q tests/test_browser_social_evidence_store.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add trading_agent/browser_social_evidence.py trading_agent/browser_social_evidence_store.py tests/test_browser_social_evidence_store.py
git commit -m "feat: persist browser social evidence lineage"
```

## Task 7: Bind autonomous browser tools to the Supervisor

**Files:**
- Create: `trading_agent/autonomous_browser_tools.py`
- Create: `tests/test_autonomous_browser_tools.py`
- Modify: `trading_agent/autonomous_supervisor_service.py`
- Modify: `tests/test_autonomous_supervisor_service.py`

- [ ] **Step 1: Write failing role, tool-count, and evidence-persistence tests**

```python
def test_browser_tools_are_role_scoped_and_total_tool_count_stays_bounded(tmp_path: Path) -> None:
    runtime = browser_tool_runtime_fixture(tmp_path)
    assert runtime.allowed_tools(AutonomousAgentRole.MARKET_OBSERVER) == (
        "browser.capture",
        "browser.follow",
        "browser.open",
        "browser.read",
        "browser.search",
        "browser.status",
        "evidence.read",
        "memory.search",
        "social.evidence.search",
        "task.history",
    )
    assert len(runtime.allowed_tools(AutonomousAgentRole.MARKET_OBSERVER)) <= 16
    assert "browser.open" not in runtime.allowed_tools(AutonomousAgentRole.TRADING)


def test_browser_read_appends_evidence_before_returning_observation(tmp_path: Path) -> None:
    runtime, evidence_store = browser_tool_runtime_with_fixture_gateway(tmp_path)
    observation = execute_tool(runtime, "browser.read", {"target_id": "target-1"})
    persisted = evidence_store.get(observation.evidence_id)
    assert persisted is not None
    assert persisted.browser_receipt_id == observation.browser_receipt_id
```

- [ ] **Step 2: Run and confirm missing bindings**

Run: `uv run pytest -q tests/test_autonomous_browser_tools.py tests/test_autonomous_supervisor_service.py`

Expected: browser tool tests fail because `trading_agent.autonomous_browser_tools` is missing.

- [ ] **Step 3: Implement the seven bindings using existing worker isolation**

Each binding’s synchronous `invoke` function creates a `LocalBrowserGatewayClient` from a path passed as an evidence ref/worker argument, sends one request, and returns canonical JSON below the existing 16 KiB tool-result cap. Add `trading_agent.autonomous_browser_tools` and the gateway/evidence modules to `worker_modules`; do not add network access outside the local Unix socket in the Supervisor process.

`browser.read` must persist a `BrowserSocialEvidence` record before returning success. Its result returns evidence ID, receipt ID, normalized URL, title, bounded excerpt, link labels/URLs, capture time, and optional screenshot digest. `social.evidence.search` reads SQLite only.

- [ ] **Step 4: Compose rather than replace foundation bindings**

Change `build_foundation_tool_runtime` to accept `browser: BrowserToolServices | None = None`, append the browser bindings only when configured, sort by name, and retain all current roles/worker modules. Schema v2 callers must still receive exactly the original three tools.

```python
bindings = (*foundation_bindings(tasks, memories), *browser_bindings(browser))
return AutonomousToolRuntime(
    tuple(sorted(bindings, key=lambda item: item.name)),
    utc_clock,
    worker_modules=frozenset(worker_modules),
)
```

- [ ] **Step 5: Pass tool and supervisor service tests**

Run: `uv run pytest -q tests/test_autonomous_browser_tools.py tests/test_autonomous_supervisor_service.py`

Expected: PASS, including unchanged schema v2 foundation-tool assertions.

- [ ] **Step 6: Commit Task 7**

```bash
git add trading_agent/autonomous_browser_tools.py trading_agent/autonomous_supervisor_service.py tests/test_autonomous_browser_tools.py tests/test_autonomous_supervisor_service.py
git commit -m "feat: give supervisor autonomous browser tools"
```

## Task 8: Keep one durable Korean browser-research agenda alive

**Files:**
- Create: `trading_agent/browser_research_agenda.py`
- Create: `tests/test_browser_research_agenda.py`

- [ ] **Step 1: Write failing continuity and freedom tests**

```python
def test_agenda_creates_one_market_context_kr_task_and_replays_idempotently(tmp_path: Path) -> None:
    services = agenda_services_fixture(tmp_path)
    first = services.ensure_open(NOW)
    second = services.ensure_open(NOW)
    assert second.task_id == first.task_id
    assert first.agent_family_id == "market_context"
    assert first.market_scope == "kr_equities"
    assert first.owner_role is AutonomousAgentRole.MARKET_OBSERVER


def test_terminal_episode_creates_a_lineage_linked_successor(tmp_path: Path) -> None:
    services = agenda_services_fixture(tmp_path)
    predecessor = services.ensure_open(NOW)
    complete_task(services.tasks, predecessor, NOW)
    successor = services.ensure_open(NOW + dt.timedelta(seconds=30))
    assert successor.task_id != predecessor.task_id
    episode = services.episodes.get_by_task(successor.task_id)
    assert episode is not None
    assert episode.predecessor_task_id == predecessor.task_id


def test_agenda_does_not_encode_a_required_browser_tool_order(tmp_path: Path) -> None:
    task = agenda_services_fixture(tmp_path).ensure_open(NOW)
    assert "browser.search" not in task.current_plan
    assert "browser.open" not in task.current_plan
    assert "browser.read" not in task.current_plan
```

- [ ] **Step 2: Run and confirm the missing-module failure**

Run: `uv run pytest -q tests/test_browser_research_agenda.py`

Expected: collection fails with `ModuleNotFoundError: trading_agent.browser_research_agenda`.

- [ ] **Step 3: Implement append-only agenda episodes and synthetic root evidence**

Each episode stores episode ID, task ID, optional predecessor task ID, root evidence ID, opened time, and canonical goal digest. The goal must direct the agent to continuously discover and test Korean-market theme/supply-demand hypotheses from social, community, news, and web sources; distinguish corroboration from reposting; use memory; and wait durably when no useful action remains. It must not name a required website, number of pages, or tool sequence.

Create a canonical `ResearchAgentEvidenceV1` with family `market_context`, market `kr_equities`, source key `browser_research_agenda.episode`, and predecessor lineage in its payload/evidence refs. Append that evidence to `ResearchAgentCycleStore` before admitting it to the Supervisor so due projection can always resolve its root evidence.

- [ ] **Step 4: Implement the continuous Supervisor wrapper**

`ContinuousBrowserResearchSupervisor` delegates every existing `DueResearchSupervisor` method unchanged. Its `run_due(now)` first calls `ensure_open(now)`, then delegates. If an episode is nonterminal it creates nothing. If the latest episode is terminal it creates exactly one successor. If the gateway is down, the task remains durable and can choose a timed wait; the agenda wrapper must not busy-loop or mark the task complete.

- [ ] **Step 5: Pass continuity tests**

Run: `uv run pytest -q tests/test_browser_research_agenda.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

```bash
git add trading_agent/browser_research_agenda.py tests/test_browser_research_agenda.py
git commit -m "feat: keep durable KR browser research agenda"
```

## Task 9: Add backward-compatible schema v3 and production wiring

**Files:**
- Modify: `trading_agent/research_agent_service_config.py`
- Modify: `trading_agent/research_agent_service_cli_args.py`
- Modify: `trading_agent/research_agent_service_builder.py`
- Modify: `run_research_agent_runtime.py`
- Modify: `tests/test_research_agent_service_cli.py`
- Modify: `tests/test_research_agent_service_runtime.py`

- [ ] **Step 1: Write failing v2 replay, v3 requirement, and builder tests**

```python
def test_schema_v2_config_remains_byte_exact_without_browser_gateway(tmp_path: Path) -> None:
    config = service_config_fixture(tmp_path)
    path = tmp_path / "service-v2.json"
    assert write_research_agent_service_config(path, config)
    payload = path.read_text()
    assert '"schema_version":2' in payload
    assert "browser_gateway_config" not in payload
    assert load_research_agent_service_config(path) == config


def test_schema_v3_requires_browser_gateway_config(tmp_path: Path) -> None:
    with pytest.raises(InvalidResearchAgentServiceConfigError):
        service_config_fixture(tmp_path).model_copy(update={"schema_version": 3})


def test_v3_builder_installs_browser_tools_and_continuous_agenda(tmp_path: Path) -> None:
    runtime = build_service_runtime(service_v3_fixture(tmp_path))
    try:
        assert runtime.supervisor_enabled
        result = runtime.tick(NOW)
        assert result.agent_family_id == "market_context"
    finally:
        runtime.close()
```

- [ ] **Step 2: Run and verify schema-v3 tests fail**

Run: `uv run pytest -q tests/test_research_agent_service_cli.py tests/test_research_agent_service_runtime.py`

Expected: new assertions fail because only schema v2 exists and the browser gateway is not wired.

- [ ] **Step 3: Extend config without changing canonical schema v2 bytes**

```python
class ResearchAgentServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2, 3] = 2
    browser_gateway_config: Path | None = None
```

The model validator requires `browser_gateway_config is None` for v2 and an absolute non-`None` path for v3. `_config_text` must exclude **only** `browser_gateway_config` when it is `None`; do not use global `exclude_none`, because existing nested nulls are part of the shipped canonical payload.

```python
payload = config.model_dump(mode="json")
if config.browser_gateway_config is None:
    del payload["browser_gateway_config"]
return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
```

LaunchAgent verification for v3 loads and verifies the referenced gateway config but does not start it. Existing schema v2 config/plist verification remains byte-identical.

- [ ] **Step 4: Add optional provision input**

Add `--browser-gateway-config PATH`. `config_from_provision_args` emits schema v2 when absent and schema v3 with the absolute path when present. The `run_research_agent_runtime.py` command surface and all existing commands remain unchanged.

- [ ] **Step 5: Wire browser services and agenda only for v3**

In `build_service_runtime`, create the cycle store first, load the verified gateway config, create the browser evidence store under `config.output_root / "autonomous-supervisor" / "browser-social-evidence.sqlite3"`, pass browser services to `build_autonomous_supervisor`, and wrap it with `ContinuousBrowserResearchSupervisor`. For v2, retain the exact current builder path and three foundation tools.

- [ ] **Step 6: Pass config, CLI, and runtime tests**

Run: `uv run pytest -q tests/test_research_agent_service_cli.py tests/test_research_agent_service_runtime.py tests/test_autonomous_supervisor_service.py tests/test_browser_research_agenda.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 9**

```bash
git add trading_agent/research_agent_service_config.py trading_agent/research_agent_service_cli_args.py trading_agent/research_agent_service_builder.py run_research_agent_runtime.py tests/test_research_agent_service_cli.py tests/test_research_agent_service_runtime.py
git commit -m "feat: wire browser computer into research service"
```

## Task 10: Add the operator CLI and fixture-level end-to-end path

**Files:**
- Create: `run_local_browser_gateway.py`
- Create: `tests/test_local_browser_gateway_cli.py`

- [ ] **Step 1: Write failing CLI help, bad input, activate, and happy-path tests**

```python
def test_help_exposes_only_gateway_operator_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(("--help",)) == 0
    output = capsys.readouterr().out
    for command in ("provision", "verify", "run", "status", "activate"):
        assert command in output
    assert "login" not in output
    assert "download" not in output


def test_status_rejects_missing_private_config(tmp_path: Path) -> None:
    assert main(("status", "--config", str(tmp_path / "missing.json"))) == 2


def test_activate_verifies_before_launchctl(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    config_path, plist_path = provisioned_gateway_fixture(tmp_path)
    assert main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda command: commands.append(command) or 0,
    ) == 0
    assert commands[0][:3] == ("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}")
```

- [ ] **Step 2: Run and confirm the missing-script failure**

Run: `uv run pytest -q tests/test_local_browser_gateway_cli.py`

Expected: collection fails because `run_local_browser_gateway.py` does not exist.

- [ ] **Step 3: Implement the five commands**

- `provision`: write immutable canonical config and plist, then print redacted verification JSON.
- `verify`: verify config/plist/executables/private paths without launching Chrome.
- `run`: acquire one gateway lease, start the socket server, own/reconnect Chrome, and serve until SIGTERM/SIGINT.
- `status`: connect through the Unix socket and print canonical readiness JSON with no page text or account data.
- `activate`: verify, require current-main authority using `current_main_commit`, then run exact `launchctl bootstrap gui/<uid> <plist>` and `launchctl kickstart gui/<uid>/ai.trading-agent.local-browser-gateway`; boot out the just-added plist if kickstart fails.

All errors return code 2 with no secret-bearing exception output. A busy gateway returns code 3.

- [ ] **Step 4: Add an in-process fixture gateway happy path**

The test starts the socket server with fake Chrome/CDP services, calls `status`, `open`, `read`, and `capture` through the real client protocol, restarts the server over the same receipt DB, replays one request, and proves no second dispatch. This is the automated end-to-end path; the next task performs actual Chrome QA.

- [ ] **Step 5: Pass CLI tests and manually exercise the CLI surface**

Run: `uv run pytest -q tests/test_local_browser_gateway_cli.py tests/test_local_browser_gateway.py`

Expected: PASS.

Run: `uv run --offline python run_local_browser_gateway.py --help`

Expected: exit 0 and display `provision`, `verify`, `run`, `status`, and `activate`.

Run: `uv run --offline python run_local_browser_gateway.py status --config /tmp/trading-agent-browser-missing.json`

Expected: exit 2 with no traceback and no file creation.

- [ ] **Step 6: Commit Task 10**

```bash
git add run_local_browser_gateway.py tests/test_local_browser_gateway_cli.py
git commit -m "feat: operate local browser gateway service"
```

## Task 11: Verify the full first subproject and deploy the local service

**Files:**
- Create: `docs/checkpoints/2026-08-26-local-agent-browser-computer-ko.md`
- Modify only if verification exposes a defect: files already listed in Tasks 1–10 and their tests

- [ ] **Step 1: Run the complete targeted test set**

```bash
uv run pytest -q \
  tests/test_local_browser_protocol.py \
  tests/test_local_browser_gateway_config.py \
  tests/test_local_chrome_controller.py \
  tests/test_chrome_devtools_client.py \
  tests/test_local_browser_gateway.py \
  tests/test_browser_social_evidence_store.py \
  tests/test_autonomous_browser_tools.py \
  tests/test_browser_research_agenda.py \
  tests/test_local_browser_gateway_cli.py \
  tests/test_autonomous_supervisor_service.py \
  tests/test_research_agent_service_cli.py \
  tests/test_research_agent_service_runtime.py
```

Expected: PASS.

- [ ] **Step 2: Run static gates for every changed Python file**

```bash
uv run ruff check \
  trading_agent/local_browser_protocol.py \
  trading_agent/local_browser_gateway_config.py \
  trading_agent/local_chrome_controller.py \
  trading_agent/chrome_devtools_client.py \
  trading_agent/local_browser_gateway.py \
  trading_agent/browser_social_evidence.py \
  trading_agent/browser_social_evidence_store.py \
  trading_agent/autonomous_browser_tools.py \
  trading_agent/browser_research_agenda.py \
  trading_agent/autonomous_supervisor_service.py \
  trading_agent/research_agent_service_config.py \
  trading_agent/research_agent_service_cli_args.py \
  trading_agent/research_agent_service_builder.py \
  run_local_browser_gateway.py \
  run_research_agent_runtime.py \
  tests/test_local_browser_protocol.py \
  tests/test_local_browser_gateway_config.py \
  tests/test_local_chrome_controller.py \
  tests/test_chrome_devtools_client.py \
  tests/test_local_browser_gateway.py \
  tests/test_browser_social_evidence_store.py \
  tests/test_autonomous_browser_tools.py \
  tests/test_browser_research_agenda.py \
  tests/test_local_browser_gateway_cli.py \
  tests/test_autonomous_supervisor_service.py \
  tests/test_research_agent_service_cli.py \
  tests/test_research_agent_service_runtime.py

uv run basedpyright \
  trading_agent/local_browser_protocol.py \
  trading_agent/local_browser_gateway_config.py \
  trading_agent/local_chrome_controller.py \
  trading_agent/chrome_devtools_client.py \
  trading_agent/local_browser_gateway.py \
  trading_agent/browser_social_evidence.py \
  trading_agent/browser_social_evidence_store.py \
  trading_agent/autonomous_browser_tools.py \
  trading_agent/browser_research_agenda.py \
  trading_agent/autonomous_supervisor_service.py \
  trading_agent/research_agent_service_config.py \
  trading_agent/research_agent_service_cli_args.py \
  trading_agent/research_agent_service_builder.py \
  run_local_browser_gateway.py \
  run_research_agent_runtime.py
```

Expected: both commands exit 0.

- [ ] **Step 3: Run project policy and diff gates**

```bash
uv run scripts/python/check-no-excuse-rules.py \
  trading_agent/local_browser_protocol.py \
  trading_agent/local_browser_gateway_config.py \
  trading_agent/local_chrome_controller.py \
  trading_agent/chrome_devtools_client.py \
  trading_agent/local_browser_gateway.py \
  trading_agent/browser_social_evidence.py \
  trading_agent/browser_social_evidence_store.py \
  trading_agent/autonomous_browser_tools.py \
  trading_agent/browser_research_agenda.py \
  trading_agent/autonomous_supervisor_service.py \
  trading_agent/research_agent_service_config.py \
  trading_agent/research_agent_service_cli_args.py \
  trading_agent/research_agent_service_builder.py \
  run_local_browser_gateway.py \
  run_research_agent_runtime.py

git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 4: Provision and verify the actual private gateway service**

Run the new `provision` command with the fixed production paths above, the actual Chrome path, current project root, and current `uv` executable. Then run:

```bash
uv run --offline python run_local_browser_gateway.py verify \
  --config /Users/goyunseo/.config/trading-agent/local-browser-gateway-v1.json \
  --plist /Users/goyunseo/Library/LaunchAgents/ai.trading-agent.local-browser-gateway-v1.plist
```

Expected: exit 0 with canonical JSON containing `"status":"verified"` and no secrets/account identifiers.

- [ ] **Step 5: Activate launchd and manually QA actual Chrome**

Activate only from committed current-main authority. Then verify:

1. `status` reports gateway and Chrome ready.
2. The dedicated Chrome profile opens independently of Codex and the terminal.
3. A public HTTPS search, open, and read returns current visible text and bounded HTTPS links.
4. `capture` creates a mode-`600` PNG and returns its digest, not PNG bytes.
5. The receipt DB contains action lineage but no cookies, headers, tokens, full HTML, account IDs, or raw auth responses.
6. An HTTP URL, a localhost URL, and a `file:` URL are rejected before Chrome navigation.
7. Closing Codex does not stop the launchd gateway.

If X/Grok access is needed, the operator manually logs into the dedicated profile. Do not automate credentials or record the login exchange.

- [ ] **Step 6: Prove restart recovery and continuous research lineage**

1. Start one v3 Research Agent tick and observe one `market_context` / `kr_equities` agenda task.
2. Record the task ID and browser receipt/evidence IDs.
3. `launchctl kickstart -k` the browser gateway service; do not delete profile/state.
4. Confirm `status` returns ready, the same profile is reused, exact request replay remains idempotent, and the durable task resumes from its stored step.
5. Terminalize only the fixture/manual test episode through its normal Supervisor result path and confirm the next tick creates exactly one predecessor-linked successor episode.
6. Confirm no KR recommendation, virtual fill, KIS/LS mutation, Alpaca request, or profitability claim was produced by this subproject.

- [ ] **Step 7: Create the Korean checkpoint**

Record:

- exact commit SHA and branch;
- gateway config/plist digests, launchd label, and redacted status;
- automated test, Ruff, basedpyright, policy, CLI, actual-Chrome, and restart-recovery commands/results;
- observed task, receipt, and evidence lineage IDs without page/account secrets;
- explicit boundary that KR decision/virtual trading and Loop Engineer are still later subprojects;
- any site that returned an honest blocked observation.

- [ ] **Step 8: Commit the checkpoint and run the final gate once more**

```bash
git add docs/checkpoints/2026-08-26-local-agent-browser-computer-ko.md
git commit -m "docs: checkpoint local agent browser computer"
git status --short
git log -1 --oneline
```

Expected: only the user’s pre-existing untracked `.codex/`, plan drafts, and `output/` remain outside commits; no implementation file is unstaged.

Do not push or replace the current production Research Agent until the implementation branch is reviewed, merged, and the user’s requested Git workflow authorizes the external write. Once merged/pushed, provision a schema-v3 Research Agent candidate that references the verified gateway config, use the existing candidate replacement/health gate, and retain schema v2 as rollback authority.

---

## Completion criteria

This plan is complete only when all of the following are observed, not merely inferred from code:

- `launchd` owns and restarts the Local Browser Gateway independently of Codex/chat/terminal state.
- The gateway controls the actual dedicated Chrome profile through loopback CDP and a current-user-only Unix socket.
- The Supervisor freely chooses among seven read-only browser/evidence tools with no hardcoded research sequence.
- Browser observations and screenshots have immutable, redacted, restart-safe receipts and evidence lineage.
- Exactly one ongoing `market_context` / `kr_equities` browser-research episode exists, and a terminal episode gets one predecessor-linked successor.
- Schema v2 remains byte-exact and operational; schema v3 is opt-in and requires a verified gateway config.
- Hostile URLs and browser mutations are rejected; no credential automation, secret storage, KIS/LS mutation, Alpaca request, KR recommendation, or real trade path is added.
- Targeted tests, Ruff, basedpyright, no-excuse policy, CLI QA, actual Chrome QA, and restart recovery all pass and are recorded in the checkpoint.

## Next approved subproject after this plan

After this first subproject is deployed and observed stable, write a separate implementation plan for **12.2 KR Autonomous Decision and Virtual Trading**. That later plan will add current-session KIS read-only price truth, recommendation admission, entry/stop/targets, deterministic position sizing, Korean virtual fills, same-bar stop precedence, immutable outcome history, dashboard/Hermes presentation, and post-outcome learning. It must consume the browser evidence produced here without turning the browser gateway into a fixed trading pipeline.
