# KR Same-Cycle Source Orchestrator Design

- Status: approved continuation of the KR Theme T0 collection contract
- Date: 2026-07-16
- Scope: one immutable KR collection date and cycle ID across OpenDART, LS NWS, KIS ranking, volume-surge derivation, and the existing DB-only coordinator

## Goal

Provide one local control-plane command which executes the existing KR source stages in this exact order:

```text
OpenDART -> LS NWS -> KIS KR ranking -> volume surge -> source-cycle coordinator
```

The command creates no domestic order, account, balance, position, quote, LLM, Opportunity, TradeSignal, or broker path.  It is a serial source-collection control plane only.

## Decisions

### Serial stages and source failures

Each provider stage and every SQLite writer runs one at a time.  A provider stage which reaches an immutable terminal `failed` source run is not treated as an orchestration crash: later stages still run so the four-source coordinator can append one incomplete cycle with the exact failure coverage.  The final command exits nonzero for that incomplete cycle.

If a stage raises before it leaves exactly one terminal source run for its source, orchestration stops immediately.  It does not invoke later providers, synthesize a failure run, or append a cycle.  This distinguishes an audited provider failure from an unsafe control-plane failure.

### Replay before provider setup

Before running any provider stage, the orchestrator reads the existing source run for the cycle.  A single, exact terminal run with the expected source ID, adapter version, and collection date is replayed without calling the stage.  A conflicting run fails closed before any provider work.  If all four source runs and the final cycle already exist, the command does no stage invocation, credential loading, HTTP, WebSocket, or provider fixture read.

OpenDART receives a date-bound `opendart-list-v2` run contract and a DB-only resume function.  Its CLI calls this resume function before opening the fixture, loading credentials, or constructing an HTTP client.  Historical `opendart-list-v1` runs remain immutable historical evidence but are incompatible with a new v2 replay for the same cycle.

### Production versus fixture mode

Fixture mode uses one explicit fixture root with these exact files:

```text
<fixture-root>/opendart/fixture-manifest.json
<fixture-root>/ls_nws/fixture-manifest.json
<fixture-root>/kis_kr_ranking/fixture-manifest.json
```

Production mode has no fixture root.  When any provider stage is missing, the requested date must equal the current KST date before the first provider call.  Full terminal replays remain permitted for historical dates because they do not open a provider.

### Result and reporting

The orchestration service returns immutable per-stage outcomes containing only source, terminal status, replay flag, and whether the coordinator appended.  The CLI writes an atomic mode-600 aggregate CSV and Korean summary with source status, record count, failure code, replay flag, and aggregate complete/incomplete state.  It never writes the cycle ID, database path, raw payload, receipt ID, hash, title, company, provider message, credential, token, or account data.

## Boundaries

- The existing `KrThemeStore` remains the only append-only source ledger and Writer lease authority.
- The existing coordinator remains the only component that appends `KrCatalystCollectionCycle`.
- The existing KIS/LS/volume collectors retain their source-specific validation.  The orchestrator only sequences them and verifies their terminal contracts.
- A complete source cycle is coverage evidence, not an eligible Opportunity day, real-time entry, shadow fill, or profitability result.

## Verification

- Unit tests prove exact order, no parallel callback, failed-source continuation to an incomplete cycle, missing-terminal abort, conflict rejection, and all-terminal no-stage replay.
- OpenDART tests prove the v2 date binding and that CLI terminal replay opens neither fixture nor credential/HTTP dependencies.
- CLI fixture E2E proves the four actual source adapters produce one complete cycle; replay rejects all provider callbacks; reports are mode 600 and redacted.
- Manual CLI QA covers `--help`, malformed cycle ID, fixture happy path, terminal replay, and an incomplete cycle.

