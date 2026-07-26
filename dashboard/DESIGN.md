# Trading Agent Observatory Design System

## 0. Research Log

- Embedded refs: shortlisted ClickHouse, PostHog, and minimalist UI → picked Taste Skill anti-slop discipline + ClickHouse technical cockpit because the product is a dense, evidence-first operations surface.
- Live reference: reviewed `tasteskill.dev` and its current v2 documentation → retained one-accent lock, compact radii, asymmetric composition, semantic-only status marks, real HTML controls, and restrained motion.
- Imagen drafts: skipped because this is an operational dashboard with no illustrative focal object; the live data hierarchy is the visual target.
- Skipped lanes: direct visual cloning — the reference is a design methodology, not the product being recreated.

## 1. Atmosphere & Identity

A quiet market operations room after the decorative screens have been removed. The signature is a thin acid-lime “live rail” that connects freshness, active agents, and current evidence while all other information remains neutral. The interface should feel exact, alert, and readable for hours.

Design read: a public-read, data-dense trading observatory with a technical and restrained language.
Snapshot ingestion remains private. Public telemetry is keyless; an authenticated device may open
an agent-specific command workspace without exposing its operator secret to page JavaScript.
`DESIGN_VARIANCE: 6`, `MOTION_INTENSITY: 3`, `VISUAL_DENSITY: 8`.

## 2. Color

### Palette

| Role | Token | Value | Usage |
|---|---|---:|---|
| Canvas | `--surface-canvas` | `#080908` | Page background |
| Primary surface | `--surface-primary` | `#0f110f` | Main panels |
| Secondary surface | `--surface-secondary` | `#151815` | Rows and controls |
| Elevated surface | `--surface-elevated` | `#1b1e1b` | Popovers and selected rows |
| Primary text | `--text-primary` | `#f3f5ed` | Headings and values |
| Secondary text | `--text-secondary` | `#a3a99e` | Labels and supporting copy |
| Tertiary text | `--text-tertiary` | `#8b9388` | Quiet metadata |
| Default border | `--border-default` | `#2d322d` | Panel containment |
| Strong border | `--border-strong` | `#454c45` | Selected and focused surfaces |
| Accent | `--accent-primary` | `#d9ff43` | Live, selected, focus, refresh |
| Accent hover | `--accent-hover` | `#e7ff86` | Interactive hover |
| Semantic good | `--status-good` | `#9ee37d` | Passing states |
| Semantic warning | `--status-warning` | `#f2c66d` | Waiting and stale states |
| Semantic error | `--status-error` | `#ff7a70` | Failed and blocked states |
| Focus | `--focus-ring` | `#d9ff43` | Keyboard focus |

### Rules

- Acid lime is the only visual accent. Semantic colors appear only beside real system state.
- Depth comes from neutral tonal shifts and sparse borders. No gradients, glow, or glass.
- Never expose broker account identity, credential state, local paths, request headers, or raw provider payloads.
- Both light and dark themes are not required because the product is a dedicated low-light operations console.

## 3. Typography

| Level | Size | Weight | Line Height | Tracking | Usage |
|---|---:|---:|---:|---:|---|
| Display | `clamp(2.3rem, 5vw, 5.5rem)` | 800 | 0.92 | `-0.055em` | Session clock |
| H1 | `1.75rem` | 720 | 1.05 | `-0.035em` | Product title |
| H2 | `1rem` | 680 | 1.2 | `-0.015em` | Panel title |
| Body | `0.875rem` | 430 | 1.5 | 0 | Default UI copy |
| Small | `0.75rem` | 520 | 1.4 | `0.01em` | Supporting labels |
| Meta | `0.6875rem` | 650 | 1.3 | `0.085em` | Uppercase metadata |
| Mono | `0.75rem` | 520 | 1.4 | `-0.01em` | Timestamps, prices, counts |

- Primary: `"Arial Narrow", "Helvetica Neue", Arial, sans-serif`.
- Mono: `"SFMono-Regular", Consolas, "Liberation Mono", monospace`.
- Max two font stacks. No externally loaded font and no serif.

## 4. Spacing & Layout

Base unit: 4px. Tokens: `--space-1` 4px, `--space-2` 8px, `--space-3` 12px, `--space-4` 16px, `--space-5` 20px, `--space-6` 24px, `--space-8` 32px, `--space-10` 40px.

- Max width: 1720px with 16px mobile, 24px tablet, and 32px desktop gutters.
- Desktop: 12 columns. Overview occupies 7 columns and evidence stream 5 columns.
- The first row is intentionally asymmetric: the clock and market state receive more width than summary counts.
- Tablet: 8 columns. Evidence follows overview.
- Mobile: one column, sticky compact header, horizontal overflow only inside data tables.

## 5. Components

### Live Rail
- Structure: `header` with product identity, freshness text, market clocks, refresh button.
- States: live, delayed, disconnected.
- Accessibility: freshness in `aria-live="polite"`; status marker is semantic and has text.
- Layout: cluster that collapses to two rows.

### State Ledger
- Structure: titled `section` and a real `table`.
- States: loading, empty, populated, stale.
- Accessibility: caption, scoped column headers, no div-based fake rows.
- Layout: table owns horizontal scroll on narrow screens.

### Metric Strip
- Structure: definition list with one featured measure and compact supporting measures.
- States: known and unavailable.
- Accessibility: full text labels and locale-formatted values.

### Evidence Item
- Structure: `article` with timestamp, symbol, strategy, recommendation levels, rationale, and evidence references.
- States: setup, active, terminal, unavailable.
- Accessibility: heading includes symbol and action; numeric labels are explicit.
- Motion: new items fade in, no looping animation.

### Filter Tabs
- Structure: `nav` containing real buttons.
- States: default, hover, active, focus.
- Accessibility: `aria-pressed` and descriptive names.

### Workspace Tabs
- Structure: persistent top `tablist` with overview, agents, account/PnL, and evidence panels.
- States: default, hover, selected, focus.
- Accessibility: real buttons, `aria-selected`, `aria-controls`, roving keyboard focus, hash
  deep-links, and one visible `tabpanel`.
- Layout: horizontal reel on narrow screens; never wraps into an ambiguous two-row nav.

### Agent Command Workspace
- Structure: selectable agent rail, identity/status detail, labeled textarea, submit action, and
  chronological interaction timeline.
- States: locked, ready, relay offline, queued, running, completed, failed.
- Accessibility: selected agent is explicit in text; `Ctrl+Enter` submits; feedback uses a polite
  live region; focus remains in the composer.
- Security: public viewers see the agent roster but never command text or responses. Commands
  require a Secure HttpOnly device session issued by a single-use publisher pairing ticket.
- Cost: model work begins only after an explicit submit. One executor runs at a time and there are
  no automatic retries.

### Paper Account Ledger
- Structure: featured equity and daily PnL, followed by realized PnL, unrealized PnL, planned open
  risk, open positions, and open orders.
- States: verified, verification incomplete, unavailable.
- Provenance: displays the paper broker and finalized session date, never the account identifier.
- Safety: account fingerprint, account number, credentials, buying power from unverified live
  responses, raw broker payloads, and request headers never cross the snapshot boundary.
- Truthfulness: values come only from the finalized lane daily ledger. Incomplete data remains
  visible with a warning label and is never presented as live broker truth.
- Cost: one public WebSocket carries snapshot events. The local relay uses filesystem
  notifications, so idle operation performs zero HTTP or database polls and invokes no paid AI
  model.

### Primitive Showcase
- `/showcase` renders live rail, ledger loading/empty/error/populated, metric strip, evidence
  states, filter tabs, and the paper account ledger at production styles.

## 6. Motion & Interaction

- Micro: 120ms ease-out for hover and press.
- Standard: 220ms ease-in-out for panel changes.
- New evidence: 320ms cubic-bezier(0.16, 1, 0.3, 1), opacity and translate only.
- Push snapshot changes over one same-origin WebSocket. Reconnect only after a disconnect with
  bounded exponential delay from 1 to 60 seconds. The macOS relay publishes an initial snapshot,
  then sends only on filesystem change; it has no periodic HTTP loop.
- Selected tabs use a 220ms opacity/translate transition and preserve the user's scroll position
  inside each workspace. Agent selection moves a 2px accent rail and updates the command context.
- Command states arrive over WebSocket; no spinner loops and no progress polling.
- Respect `prefers-reduced-motion`; remove all transforms and transitions.

## 7. Depth & Surface

Strategy: tonal shift with sparse borders.

- Canvas → primary → secondary → elevated is the complete surface ladder.
- Panels use one 1px boundary and 4px radius.
- Rows use either a surface shift or a bottom divider, never a box around every cell.
- No drop shadows. Focus uses a 2px accent outline with 2px offset.

## 8. Accessibility Constraints & Accepted Debt

- Minimum body size 14px and minimum interactive target 40px.
- WCAG AA contrast for all persistent text.
- Keyboard access for refresh and filters.
- Keyboard access for workspace tabs, agent selection, and command submission.
- Never use color as the only state signal.
- Live updates use polite announcements and do not steal focus.
- No screen-reader chart sonification is required because the product uses tables and definition
  lists instead of canvas charts.
