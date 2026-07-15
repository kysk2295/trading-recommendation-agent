# LS NWS Read-Only News Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LS증권 NWS001 실시간 뉴스 제목을 주문 기능 없이 raw-first KR `news` source run으로 수집하는 fixture-verifiable CLI를 만든다.

**Architecture:** mode-600 credential loader와 exact OAuth client가 sanitized access token을 만들고, NWS-only WebSocket wrapper가 bounded raw frame을 전달한다. collector는 terminal restart를 먼저 확인한 뒤 frame bytes를 기존 KR source receipt에 append하고 strict parser 결과를 canonical NEWS catalyst와 observation lineage로 연결한다.

**Tech Stack:** Python 3.12, httpx2, websockets sync client, Pydantic v2, SQLite append-only KR ledger, Typer, pytest, Ruff, basedpyright

---

## File Map

- Create `trading_agent/ls_config.py`: LS secret file 검증, 고정 REST endpoint와 production HTTP client
- Create `trading_agent/ls_token.py`: exact OAuth POST와 sanitized access-token parser
- Create `trading_agent/ls_nws.py`: raw frame/NWS packet models, duplicate-key-safe strict parser, canonical payload
- Create `trading_agent/ls_nws_stream.py`: exact NWS-only WebSocket subscription과 bounded frame receiver
- Create `trading_agent/ls_nws_collection.py`: lazy opener, raw-first append, catalyst lineage와 terminal source run
- Create `trading_agent/ls_nws_fixture.py`: path-contained frame manifest loader와 finite receiver
- Create `run_ls_nws_collect.py`: production/fixture CLI와 aggregate-only mode-600 report
- Create `tests/test_ls_config.py`
- Create `tests/test_ls_token.py`
- Create `tests/test_ls_nws.py`
- Create `tests/test_ls_nws_stream.py`
- Create `tests/test_ls_nws_collection.py`
- Create `tests/test_ls_nws_fixture.py`
- Create `tests/test_ls_nws_collect_cli.py`
- Create `tests/fixtures/ls_nws/fixture-manifest.json`
- Create `tests/fixtures/ls_nws/frame-000001.json`
- Create `tests/fixtures/ls_nws/frame-000002.json`
- Modify `pyproject.toml`: 새 CLI를 basedpyright include에 추가
- Modify `AGENTS.md`: LS secret path와 LS account/order/WebSocket account-registration 금지 명시
- Modify `README.md`: LS NWS source 현황, 명령과 다음 read-only market-data/indicator 순서 기록
- Create `docs/checkpoints/2026-07-15-ls-nws-readonly-collector-ko.md`: 구현·검증·외부 호출 0건 체크포인트

### Task 1: LS Credential Boundary

**Files:**
- Create: `tests/test_ls_config.py`
- Create: `trading_agent/ls_config.py`

- [x] **Step 1: Write failing secret-loader tests**

`test_load_ls_credentials_accepts_only_exact_private_file`, mode mismatch, symlink, owner/regular-file guard, invalid UTF-8, duplicate·extra·missing setting, whitespace/non-printable/length bound와 credential repr redaction을 각각 검증한다. 테스트 값은 synthetic marker만 사용한다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_config.py`

Expected: FAIL during import because `trading_agent.ls_config` does not exist.

- [x] **Step 3: Implement minimal strict loader and HTTP client factory**

Add exact constants `LS_REST_BASE_URL` and `DEFAULT_LS_SECRET_PATH`, frozen repr-redacted credentials, safe exception strings, `lstat`/owner/regular/symlink/exact-600 checks, exact two-setting parser and bounded printable ASCII validation. `create_ls_http_client()` uses exact base URL, TLS verification default, bounded pool/timeouts and `follow_redirects=False`.

- [x] **Step 4: Run focused tests and static checks**

Run: `uv run pytest -q tests/test_ls_config.py`

Run: `uv run ruff check trading_agent/ls_config.py tests/test_ls_config.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_config.py tests/test_ls_config.py
git commit -m "feat: add guarded LS credential config"
```

### Task 2: Exact OAuth Token Client

**Files:**
- Create: `tests/test_ls_token.py`
- Create: `trading_agent/ls_token.py`

- [x] **Step 1: Write failing OAuth contract tests**

Injected `httpx2.MockTransport`로 exact `POST /oauth2/token`, no query secret, form fields, content type, no redirect client를 검증한다. wrong endpoint/client, non-200, wrong content type, empty/oversize/malformed JSON, invalid token과 network exception이 sanitized 오류를 내고 key·secret·token·provider message를 렌더링하지 않는 테스트를 쓴다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_token.py`

Expected: FAIL because token client symbols are missing.

- [x] **Step 3: Implement minimal token client**

Create repr-redacted `LsAccessToken`, exact endpoint/redirect guards and `issue_ls_access_token()`. Use form body with the four official fields, cap response bytes, parse an object without rendering it, validate the token, and convert transport/provider failures to safe typed errors.

- [x] **Step 4: Run focused tests and static checks**

Run: `uv run pytest -q tests/test_ls_config.py tests/test_ls_token.py`

Run: `uv run ruff check trading_agent/ls_config.py trading_agent/ls_token.py tests/test_ls_config.py tests/test_ls_token.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_token.py tests/test_ls_token.py
git commit -m "feat: add sanitized LS OAuth client"
```

### Task 3: Strict NWS Packet Parser

**Files:**
- Create: `tests/test_ls_nws.py`
- Create: `trading_agent/ls_nws.py`

- [x] **Step 1: Write failing parser tests**

Official header/body shape의 text와 binary raw frame, valid KST timestamp, flat canonical payload와 `ls-nws://news/<realkey>` identity를 검증한다. duplicate JSON key, malformed UTF-8/JSON, extra/missing field, wrong TR/key, invalid date/time, non-24-digit realkey, invalid id/bodysize/code/title, collection-date mismatch와 future publication을 각각 거부하는 테스트를 쓴다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_nws.py`

Expected: FAIL because parser models do not exist.

- [x] **Step 3: Implement parser and canonicalizer**

Add `LsNwsWireKind`, repr-hidden `LsNwsRawFrame`, strict Pydantic packet models, duplicate-key-detecting JSON loader, KST timestamp builder and sorted compact UTF-8 canonical payload. Errors expose only stable failure codes.

- [x] **Step 4: Run focused tests and static checks**

Run: `uv run pytest -q tests/test_ls_nws.py`

Run: `uv run ruff check trading_agent/ls_nws.py tests/test_ls_nws.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_nws.py tests/test_ls_nws.py
git commit -m "feat: parse LS NWS packets strictly"
```

### Task 4: NWS-Only WebSocket Transport

**Files:**
- Create: `tests/test_ls_nws_stream.py`
- Create: `trading_agent/ls_nws_stream.py`

- [x] **Step 1: Write failing stream tests**

Fake connection으로 exact initial/final URL guard가 token 전송 전에 실행되는지, 보낸 JSON이 `tr_type=3`, `NWS`, `NWS001`만 포함하는지 검증한다. account registration `1/2`, 다른 TR과 order/account 문자열 부재, text/binary bytes 보존, receive timestamp, timeout `None`, invalid timeout과 connection 오류 redaction을 테스트한다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_nws_stream.py`

Expected: FAIL because stream module does not exist.

- [x] **Step 3: Implement bounded exact stream wrapper**

Use `websockets.sync.client.connect` with proxy/compression disabled, bounded timeouts/max size/max queue and no user-agent. Verify final URL from TLS socket request before sending canonical subscription. Expose only `receive_frame(timeout_seconds)` and sanitize handshake/close/socket failures.

- [x] **Step 4: Run focused tests and static checks**

Run: `uv run pytest -q tests/test_ls_nws.py tests/test_ls_nws_stream.py`

Run: `uv run ruff check trading_agent/ls_nws.py trading_agent/ls_nws_stream.py tests/test_ls_nws.py tests/test_ls_nws_stream.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_nws_stream.py tests/test_ls_nws_stream.py
git commit -m "feat: add NWS-only LS stream"
```

### Task 5: Raw-First Collection State Machine

**Files:**
- Create: `tests/test_ls_nws_collection.py`
- Create: `trading_agent/ls_nws_collection.py`

- [x] **Step 1: Write failing collector tests**

Lazy receiver opener와 temporary `KrThemeStore`로 success, zero-news success, raw receipt before parser, `http_status=101`, wire-kind request key, canonical catalyst/observation lineage, max-frame completion을 검증한다. malformed/date/future/duplicate/stream failures이 receipt와 partial rows를 보존한 failed run으로 끝나는지, terminal success와 failed restart가 opener를 0회 호출하는지 테스트한다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_nws_collection.py`

Expected: FAIL because collector does not exist.

- [x] **Step 3: Implement minimal lazy collector**

Add protocol-based receiver/opener, adapter version `ls-nws-v1`, safe input bounds and `<cycle>:news` restart lookup. Append each frame receipt before parser, then canonical catalyst and receipt link. Compute terminal run from stored evidence and map only declared parser/transport failures to stable failure codes.

- [x] **Step 4: Run focused ledger/collector tests**

Run: `uv run pytest -q tests/test_ls_nws_collection.py tests/test_kr_theme_store.py tests/test_kr_source_cycle.py`

Run: `uv run ruff check trading_agent/ls_nws_collection.py tests/test_ls_nws_collection.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_nws_collection.py tests/test_ls_nws_collection.py
git commit -m "feat: collect LS news raw first"
```

### Task 6: Finite Synthetic Fixture

**Files:**
- Create: `tests/test_ls_nws_fixture.py`
- Create: `trading_agent/ls_nws_fixture.py`
- Create: `tests/fixtures/ls_nws/fixture-manifest.json`
- Create: `tests/fixtures/ls_nws/frame-000001.json`
- Create: `tests/fixtures/ls_nws/frame-000002.json`

- [x] **Step 1: Write failing fixture tests**

Committed manifest가 순서대로 exact raw bytes와 fixed timestamp/wire kind를 내고 마지막에 `None`을 반환하는지 검증한다. absolute/traversal/symlink path, duplicate/gapped sequence, empty payload, invalid timestamp/wire kind와 extra field를 거부하는 테스트를 쓴다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_nws_fixture.py`

Expected: FAIL because fixture loader does not exist.

- [x] **Step 3: Implement fixture loader and synthetic frames**

Pydantic manifest model과 path containment 검사를 추가한다. fixture receiver는 network/token interface와 같은 `receive_frame()`만 제공하고 synthetic Korean market headlines in official packet shape를 사용한다.

- [x] **Step 4: Run fixture and collection tests**

Run: `uv run pytest -q tests/test_ls_nws_fixture.py tests/test_ls_nws_collection.py`

Run: `uv run ruff check trading_agent/ls_nws_fixture.py tests/test_ls_nws_fixture.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add trading_agent/ls_nws_fixture.py tests/test_ls_nws_fixture.py tests/fixtures/ls_nws
git commit -m "test: add deterministic LS NWS fixture"
```

### Task 7: Redacted Collection CLI

**Files:**
- Create: `tests/test_ls_nws_collect_cli.py`
- Create: `run_ls_nws_collect.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Write failing CLI tests**

Direct `main()` tests로 invalid cycle/date/duration/frame cap이 DB 생성 전에 실패하는지, fixture+secret 조합을 거부하는지, committed fixture success와 restart가 aggregate-only report를 만드는지 검증한다. title/code/realkey/id/hash/key/secret/token marker 비노출, failed source run 보존·nonzero와 DB/report exact mode `600`도 테스트한다.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_ls_nws_collect_cli.py`

Expected: FAIL because CLI module does not exist.

- [x] **Step 3: Implement production and fixture composition**

Preflight valid input, then terminal restart lookup을 credential load보다 먼저 수행한다. production lazy opener는 strict secret loader, exact OAuth client와 exact NWS stream만 조합한다. fixture opener는 committed raw frames만 사용한다. Write aggregate Korean report atomically with mode `600`; terminal failure returns nonzero.

- [x] **Step 4: Add CLI to basedpyright and run focused gates**

Run: `uv run pytest -q tests/test_ls_config.py tests/test_ls_token.py tests/test_ls_nws.py tests/test_ls_nws_stream.py tests/test_ls_nws_collection.py tests/test_ls_nws_fixture.py tests/test_ls_nws_collect_cli.py`

Run: `uv run ruff check run_ls_nws_collect.py trading_agent/ls_*.py tests/test_ls_*.py`

Run: `uv run basedpyright run_ls_nws_collect.py trading_agent/ls_config.py trading_agent/ls_token.py trading_agent/ls_nws.py trading_agent/ls_nws_stream.py trading_agent/ls_nws_collection.py trading_agent/ls_nws_fixture.py`

Expected: PASS with 0 errors/warnings.

- [x] **Step 5: Commit**

```bash
git add run_ls_nws_collect.py pyproject.toml tests/test_ls_nws_collect_cli.py
git commit -m "feat: add LS NWS collection CLI"
```

### Task 8: Documentation and Manual QA

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Create: `docs/checkpoints/2026-07-15-ls-nws-readonly-collector-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-ls-nws-readonly-collector.md`

- [x] **Step 1: Update product and secret boundaries**

Document exact LS secret path, NWS implemented status, exposed-key rotation requirement, no LS account/order support, fixture command and next LS read-only bar/supply/local-indicator milestones. Do not include any credential values.

- [x] **Step 2: Run manual CLI QA without network**

Run: `uv run python run_ls_nws_collect.py --help`

Run one invalid date/cycle invocation and verify exit 2 with no DB.

Run committed fixture into a temporary output path, rerun the same cycle, and verify first/restart counts, report/database mode `600` and private fixture markers absent from terminal/report.

- [x] **Step 3: Run full fresh verification**

Run: `uv run pytest -q`

Run: `uv run ruff check .`

Run: `uv run basedpyright`

Run: `git diff --check`

Expected: all tests pass, Ruff pass, basedpyright 0 errors/warnings, diff check clean.

- [x] **Step 4: Record checkpoint evidence**

Write exact test count and CLI observations. State actual LS/OpenDART/KIS/Alpaca/LLM/broker/external-message calls were 0 and the fixture is not recommendation quality or profitability evidence.

- [x] **Step 5: Commit documentation**

```bash
git add AGENTS.md README.md docs/checkpoints/2026-07-15-ls-nws-readonly-collector-ko.md docs/superpowers/plans/2026-07-15-ls-nws-readonly-collector.md
git commit -m "docs: record LS NWS collector milestone"
```

### Task 9: Review, Integrate, and Verify Main

**Files:**
- Review all files listed above

- [x] **Step 1: Review the complete diff**

Check for credential literals, `/stock/accno`, `/stock/order`, `tr_type` 1/2, arbitrary endpoint inputs, raw payload logging, non-atomic reports, hidden network in fixture/restart and scope creep. Fix findings through new RED/GREEN tests.

- [x] **Step 2: Re-run targeted and full gates after review fixes**

Run the focused LS suite, `uv run pytest -q`, `uv run ruff check .`, `uv run basedpyright`, manual CLI fixture QA and `git diff --check` again.

- [ ] **Step 3: Push the feature branch and integrate to main**

Use the repository's established non-interactive worktree integration process. Preserve any unrelated user changes, fast-forward or merge only the reviewed feature commits, then push `origin/main`.

- [ ] **Step 4: Verify merged main freshly**

On main run `git status --short --branch`, full pytest, Ruff, basedpyright, CLI help/bad/fixture/restart QA and confirm `origin/main` contains the checkpoint commit.

- [ ] **Step 5: Mark this plan complete**

Change every completed checkbox to `[x]`, commit the plan-only update, push `origin/main`, and report the exact merged HEAD and verification evidence.
