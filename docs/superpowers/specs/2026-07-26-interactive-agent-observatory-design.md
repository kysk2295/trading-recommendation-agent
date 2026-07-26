# Interactive Agent Observatory Design

## Goal

Turn the public trading observatory into a legible tabbed workspace where Ko Yunseo can:

1. understand market and forward-session status at a glance;
2. select each runtime agent and give it an asynchronous natural-language goal;
3. see command acceptance, execution state, and the final response without polling;
4. inspect Paper account PnL and source-backed recommendations in focused views.

The dashboard remains a Paper/research operations surface. It does not add live-money order
controls or bypass repository, broker, provider, and market-time safety gates.

## Approaches considered

### A. Public command endpoint

Fastest to build, but anyone with the URL could spend model budget or cause local mutations.
Rejected.

### B. Separate Tailscale command dashboard

Strong network boundary, but splits the most important workflow away from the Railway
observatory and makes mobile use depend on a second product surface. Rejected.

### C. Public observatory plus device-bound command session

Selected. Reading remains public and keyless. A trusted device receives a Secure, HttpOnly,
SameSite cookie through a single-use pairing ticket issued over the authenticated Mac mini
publisher connection. No access key is typed into the UI.

## Information architecture

The persistent top navigation has four tabs:

- **개요**: market clock, market states, forward quality, blockers, research foundation.
- **에이전트**: agent roster, selected-agent detail, command composer, command/response timeline.
- **계좌·PnL**: finalized Paper ledger, provenance, quality state, exposure counts.
- **추천·근거**: immutable recommendations and source-backed evidence filters.

Tabs use real buttons with `role="tab"`, roving keyboard focus, `aria-selected`, and URL hashes.
Only the selected panel participates in the accessibility tree. Snapshot rendering updates every
panel even when it is not visible, so changing tabs never triggers a data request.

## Command boundary

### Pairing

- The Mac mini publisher authenticates with the existing ingest token and asks Railway for a
  single-use, short-lived pairing ticket.
- Visiting the ticket URL sets an operator cookie and redirects to `/#agents`.
- The long-lived operator secret never appears in page JavaScript, a form, localStorage, logs, or
  the URL.
- Public snapshot and viewer WebSocket routes remain unauthenticated.

### Submission

- `POST /api/agents/:agentId/interactions` requires the operator cookie.
- The request boundary accepts a non-empty command up to 2,000 characters.
- Railway writes an immutable interaction receipt and immediately pushes it to the one
  authenticated publisher WebSocket.
- Only one Mac mini command is executed at a time. There are no automatic model calls.

### Execution and response

- The publisher invokes a fresh Hermes CLI turn using an argv array, never a shell string.
- Every goal includes the selected dashboard-agent identity, the integration worktree, and the
  requirement to obey `AGENTS.md`.
- Completion or failure is returned on the same WebSocket, stored, and pushed to authenticated
  viewers.
- Responses are bounded and redacted. Public viewers do not receive command text or responses.

## Interaction states

The command workspace defines:

- **locked**: public telemetry remains visible; command form explains that this device is not
  paired.
- **ready**: selected agent and safety scope are visible; submit is enabled.
- **queued**: immutable receipt created, waiting for the single local executor.
- **running**: Hermes process is active.
- **completed**: final response and completion time visible.
- **failed**: direct error copy with a retry-by-resubmission path; no automatic paid retry.
- **relay offline**: submission is rejected before creating a misleading executable receipt.

Submit feedback uses `aria-live="polite"` and never steals focus. `Ctrl+Enter` submits; Enter
alone creates a new line.

## Cost contract

- Browser: one initial snapshot GET, one public snapshot WebSocket, and an authenticated
  interaction WebSocket only for a paired device.
- Publisher: one persistent authenticated WebSocket and filesystem notifications.
- Database: reads on connection or user action; writes on snapshot or interaction events.
- No 10-second, 15-second, or other periodic HTTP/DB polling.
- No model call without an explicit command submission.

## Verification

- Unit/integration: strict schemas, cookie authorization, single-use pairing, interaction store,
  publisher delivery, result broadcast, invalid input, relay-offline behavior.
- E2E: paired browser submits a harmless agent command and receives its terminal response.
- UI: 375, 768, and 1280 px; tabs, keyboard path, locked/queued/running/completed/failed states,
  long Korean text, empty interactions, reduced motion, 200% zoom.
- Security: public read works without a key; unauthenticated command is `401`; unpaired viewers
  never receive interaction payloads; publisher remains Bearer protected.
- Cost: clear network capture, wait 20 seconds while idle, assert zero HTTP requests and unchanged
  snapshot/interaction timestamps.

