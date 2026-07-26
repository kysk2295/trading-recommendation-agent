# Ember Operations Workstation Design System

## 0. Research and audit log

- Approved direction: the 2026-07-26 dashboard v2 handoff fixes the product as a carbon-black operations workstation with one ember-orange accent, compact asymmetric financial composition, fixed desktop rails, and evidence-first interaction.
- Existing-surface audit: dashboard v1 is a four-tab observatory with acid-lime tokens, system fonts, a sticky document header, and a showcase containing sample values. V2 replaces its visual and information architecture while preserving the Hono, Bun, vanilla TypeScript, WebSocket, public-read, and private-operator boundaries.
- Reference use: the user-provided finance dashboard pages are visual inspiration only. They contribute density, table/chart rhythm, and asymmetric composition; no mark, logo, wording, layout copy, or asset may be reproduced.
- Frontend guidance: redesign audit establishes targeted replacement rather than framework migration; layout mechanics establish bounded shell and named scroll owners; perfection establishes same-origin fonts, semantic HTML, real-browser accessibility/performance gates; designpowers establishes inclusive personas, cognitive constraints, objective review, and explicit debt.
- Explicit anti-references: glass, neon, AI purple, equal-card mosaics, nested cards, decorative status dots, fake precision, and perpetual motion.

## 1. Atmosphere, identity, and operators

Ember Operations Workstation is a carbon-black research control room: compact, calm, and legible
for a long session. Its signature is an ember edge that appears only on the current route, the
current focus, or a user-invoked Evidence Trace. The memorable moment is opening any value and
watching its evidence path resolve from a source receipt to a Reviewer, Paper, or explicit blocker
terminal without changing the workspace underneath.

`DESIGN_VARIANCE: 5`, `MOTION_INTENSITY: 3`, `VISUAL_DENSITY: 9`.

Primary operators and pass conditions:

| Persona | Context | Must be able to do |
| --- | --- | --- |
| Research operator | dense desktop, long sessions, keyboard-first | compare causal evidence, trials, Reviewer decisions, and lifecycle without losing workspace or trace focus |
| Mobile incident responder | 375px viewport, one hand, intermittent connection | locate a blocker, open its trace, and distinguish stale, unavailable, and valid empty without horizontal page scroll |
| Low-vision operator | 200% zoom, increased contrast, keyboard or screen reader | reach all nine workspaces, read every state in text, and close the trace drawer with focus returned |
| Motion-sensitive operator | `prefers-reduced-motion: reduce` | receive every state and navigation change with no transform animation or auto motion |
| Korean/English mixed-content operator | CJK and long SHA/code strings | read or deliberately truncate bounded content without layout breakage or ambiguous ellipses |

Safety and truth outrank visual taste. This surface is a research and Alpaca Paper observatory; it
never represents replay, synthetic, or backtest output as profitability and never opens a
real-money path.

## 2. Color

Dark mode is the only theme. Every production color must use one of these tokens.

| Role | Token | Value | Contract |
| --- | --- | --- | --- |
| Canvas | `--surface-canvas` | `#070706` | viewport and outer shell |
| Shell | `--surface-shell` | `#0B0B09` | fixed navigation and context rails |
| Primary | `--surface-primary` | `#10100D` | workspace background |
| Secondary | `--surface-secondary` | `#171611` | grouped rows and controls |
| Elevated | `--surface-elevated` | `#1E1C16` | drawer, menu, selected data row |
| Scrim | `--surface-scrim` | `rgba(7, 7, 6, 0.76)` | modal drawer isolation only |
| Text primary | `--text-primary` | `#F3F0E7` | headings and primary values |
| Text secondary | `--text-secondary` | `#BBB4A6` | body and supporting labels |
| Text tertiary | `--text-tertiary` | `#918A7D` | metadata; never required information alone |
| Text inverse | `--text-inverse` | `#120B06` | text on ember controls |
| Border subtle | `--border-subtle` | `#24221C` | row separation |
| Border default | `--border-default` | `#343127` | panel and control boundary |
| Border strong | `--border-strong` | `#514B3C` | selected neutral surface |
| Ember soft | `--ember-soft` | `#30170A` | selected-route background |
| Ember | `--ember-500` | `#FF6B1A` | sole product accent and focus |
| Ember hover | `--ember-400` | `#FF8547` | hover |
| Ember pressed | `--ember-600` | `#DD4F08` | active/pressed |
| Success | `--status-success` | `#71C995` | verified/passed only |
| Warning | `--status-warning` | `#E6B85C` | stale/incomplete only |
| Error | `--status-error` | `#F1786E` | corrupt/error/blocked only |
| Neutral status | `--status-neutral` | `#A9A293` | loading/empty/unavailable |
| Focus | `--focus-ring` | `#FF8547` | 2px keyboard focus outline |

Rules:

- Ember is the only product accent. Green, amber, and red communicate verified semantic state
  only; no decorative colored dot is permitted.
- Filled ember controls use `--text-inverse`; primary text must not sit on ember without measured
  AA contrast.
- Depth is tonal shift plus sparse borders. No gradients, glow, glass, blur, or drop shadows.
- Charts use neutral line/area ramps and ember only for the selected series. Positive/negative
  colors are legal only when the value itself has that semantic.
- State always has a text label and optional icon; color is never the only signal.

## 3. Typography and font assets

### Same-origin asset and license contract

| Family | Runtime files | License artifact | Loading contract |
| --- | --- | --- | --- |
| Pretendard Variable | `/assets/fonts/PretendardVariable.woff2` | `/assets/licenses/Pretendard-LICENSE.txt` | checked-in upstream release asset, SIL Open Font License 1.1, `font-weight: 45 920`, preload this single critical file, `font-display: swap` |
| IBM Plex Mono | `/assets/fonts/IBMPlexMono-Regular.woff2`, `IBMPlexMono-Medium.woff2`, `IBMPlexMono-SemiBold.woff2` | `/assets/licenses/IBM-Plex-LICENSE.txt` | checked-in upstream release assets, SIL Open Font License 1.1, `font-display: swap`; preload Regular only |

`dashboard/public/assets/fonts/` and `dashboard/public/assets/licenses/` are the only production font
and license locations. The implementation commit records upstream release/tag, SHA-256 for every
binary and license, and the unmodified license text. There is no CDN, remote font request, runtime
fetch fallback, or base64 embedding. A missing or hash-mismatched font is a build failure, not a
silent substitution. CSS fallbacks are `"Apple SD Gothic Neo", "Noto Sans KR", system-ui,
sans-serif` and `"SFMono-Regular", Consolas, monospace`.

### Type scale

| Token | Size | Weight | Line height | Tracking | Use |
| --- | ---: | ---: | ---: | ---: | --- |
| `--type-display` | `clamp(2rem, 3.8vw, 3.75rem)` | 760 | 0.98 | `-0.045em` | one workspace key value, never a marketing hero |
| `--type-h1` | `1.5rem` | 720 | 1.12 | `-0.025em` | workspace title |
| `--type-h2` | `1rem` | 680 | 1.25 | `-0.012em` | region heading |
| `--type-h3` | `0.875rem` | 650 | 1.35 | `-0.006em` | row group/card heading |
| `--type-body` | `0.875rem` | 430 | 1.5 | `0` | default UI text; minimum |
| `--type-small` | `0.75rem` | 500 | 1.45 | `0.005em` | secondary copy |
| `--type-meta` | `0.6875rem` | 620 | 1.35 | `0.065em` | short labels, sentence case preferred |
| `--type-mono` | `0.75rem` | 500 | 1.45 | `-0.005em` | timestamps, prices, IDs, SHAs |

Pretendard owns all prose and UI labels. IBM Plex Mono owns tabular numbers, timestamps, versions,
blocker codes, evidence IDs, and short hashes. Numeric columns use `font-variant-numeric:
tabular-nums slashed-zero`. Body copy never drops below 14px. Long prose is limited to 68ch; long
IDs use middle truncation with the complete safe value available to copy and to assistive text.

## 4. Spacing, shell, and scroll ownership

### Tokens

Base unit is 4px.

| Token | Value | Intended use |
| --- | ---: | --- |
| `--space-1` | `4px` | icon/label micro gap |
| `--space-2` | `8px` | compact inline gap |
| `--space-3` | `12px` | dense row padding |
| `--space-4` | `16px` | normal panel inset |
| `--space-5` | `20px` | comfortable group inset |
| `--space-6` | `24px` | workspace gutter |
| `--space-8` | `32px` | region separation |
| `--space-10` | `40px` | major separation |
| `--space-12` | `48px` | mobile navigation clearance |
| `--control-sm` | `32px` | dense desktop control; not primary touch action |
| `--control-md` | `40px` | standard control |
| `--control-touch` | `44px` | mobile and primary action minimum |
| `--radius-inner` | `2px` | row selection and code |
| `--radius-control` | `4px` | buttons and fields |
| `--radius-panel` | `6px` | top-level panel only |
| `--layer-base` | `0` | workspace |
| `--layer-rail` | `10` | fixed rails |
| `--layer-drawer` | `30` | Evidence Trace |
| `--layer-toast` | `40` | bounded feedback |

### Fixed desktop shell

- At `>= 1180px`, `.workstation-shell` is a `100dvb` grid:
  `232px minmax(0, 1fr) 304px`; rows are `64px minmax(0, 1fr) 28px`.
- The sidebar occupies column 1 across all rows and never scrolls with workspace content. If its
  navigation cannot fit vertically, only `.workspace-nav-list` owns overflow.
- The command/context header occupies column 2, row 1. The context rail occupies column 3 across
  rows 1–2 and owns its own overflow only for Evidence/selection context.
- `.workspace-scroll-body` occupies column 2, row 2, has `min-block-size: 0`,
  `min-inline-size: 0`, and is the sole vertical scroll owner for the active workspace.
- The status strip is fixed in row 3. `html` and `body` do not scroll in desktop shell mode.
- Regions use `stack`, `cluster`, `switcher`, `fixed-sidenav-shell`, `scroll-body-shell`,
  `list-detail`, and overflow-safe grids:
  `repeat(auto-fit, minmax(min(16rem, 100%), 1fr))`.
- Tables may own horizontal overflow in a labeled, keyboard-focusable `.table-viewport`; primary
  page content never owns two-dimensional scroll.

### Responsive shell

| Width | Shell behavior | Scroll owner |
| --- | --- | --- |
| `>=1180px` | 232px fixed sidebar + fluid workspace + 304px fixed context rail | active `.workspace-scroll-body`; context rail only for its own long content |
| `768–1179px` | 72px icon/short-label rail, fixed 56px header; context rail becomes an end drawer | active workspace body; open drawer traps focus and owns its body scroll |
| `320–767px` | fixed 52px header and 56px bottom workspace launcher; no persistent side/context rail | one active workspace body; tables/reels declare local horizontal scroll |

At 375px the content reflows to one readable column with no page-level horizontal overflow. At
200% zoom, the tablet/mobile shell may engage based on available space rather than device identity.
Components prefer container queries and intrinsic wrapping; the shell alone uses viewport media
queries. Safe-area insets are applied to the mobile header and launcher.

## 5. Information architecture, primitives, and states

### Nine workspace content jobs

| Name / route | Job | Required content |
| --- | --- | --- |
| Command Center / `#command-center` | act | persistent agent conversations, operator boundary, exactly-once receipt lifecycle; default route |
| Overview / `#overview` | orient | market/session posture, blockers, active research/Paper/System summaries; no duplicated full tables |
| Markets / `#markets` | observe | completed-bar/session context, licensed quote capability, bounded market tables/charts |
| Data Sources / `#data-sources` | verify inputs | FRED/ALFRED, Treasury, CFTC, OpenDART, KIS, LS, Alpaca capability, entitlement, freshness, coverage, receipt, blocker |
| Research / `#research` | evaluate evidence | source/paper/hypothesis queue, causal dataset SHA, evidence gaps |
| Strategies / `#strategies` | govern lifecycle | lane/version/trial, walk-forward, overfit diagnostics, Reviewer, champion, Allocation Manager lock |
| Derivatives / `#derivatives` | inspect research context | option chain, IV, skew, term structure, futures security master, roll and CFTC context |
| Paper / `#paper` | reconcile Paper state | finalized Paper PnL, positions/orders, entry/OCO/reconcile/cutoff/EOD-flat lifecycle |
| System / `#system` | operate runtime | M0–M10, launchd typed receipts, PID/exit, stage results, Railway health/deploy, event relay |

### Canonical source state

Every independently sourced section has exactly one of:

| State | Meaning and visual behavior |
| --- | --- |
| `loading` | initial reader or route projection is pending; shape-matched skeleton, `aria-busy=true`; never shown during ordinary WebSocket idle |
| `empty` | authoritative reader succeeded for the selected point in time and returned zero valid rows; explicit “0 records” copy and trace to the successful receipt |
| `error` | bounded read/parse operation failed for a known transient or typed cause; sanitized error code and retry-by-new-event guidance |
| `blocked` | data exists but a named safety, quality, entitlement, lifecycle, or Paper gate prevents use; blocker code and blocker terminal required |
| `unavailable` | authority, entitlement, or required current receipt does not exist; no zero or estimate substitutes for the missing value |
| `corrupt` | schema, hash, append-only, time-order, or cross-reference validation failed; affected section fails closed and never falls back to an older “healthy” value silently |
| `stale` | last valid observation exists but exceeds that field’s declared freshness/point-in-time rule; timestamp and age remain visible, current/actionable styling is removed |
| `populated` | reader, schema, freshness, and redaction checks pass; all rows carry trace IDs and bounded/truncation metadata |

Precedence is `corrupt > error > blocked > unavailable > stale > populated | empty`; `loading` is
only the temporary pre-result state. A valid empty result and a failed read are never represented by
the same state. Mixed-state workspaces preserve section-local states instead of collapsing the
whole page to a healthy banner.

All collection primitives expose `total_count`, `projected_count`, and `truncated`. Default caps
are 50 table rows, 24 chart points, 12 feed items, 8 targets/evidence badges, 160 visible summary
characters, 2,000 command characters, and 8,000 redacted response characters. A workspace-specific
lower cap in the master spec wins.

### Reusable primitives

| Primitive | Structure and variants | Required behavior |
| --- | --- | --- |
| `WorkstationShell` | sidebar, context header/rail, active main, status strip | fixed-shell geometry above; route landmarks; one active main scroll owner |
| `WorkspaceNav` | nine real links/buttons with route index and optional text status | active, hover, pressed, focus, unavailable; roving Arrow/Home/End; hash reload/back-forward |
| `ContextRail` | selection summary or trace launcher; drawer variant at narrower widths | never repeats the whole workspace; absent selection is truthful empty |
| `SourceStatePanel` | heading, observation time, state copy, content, trace action | all eight canonical states; no spinner loop; state label is text |
| `MetricCell` | `dl` pair plus source time and trace button | known, stale, blocked, unavailable; mono/tabular numbers; no fake precision |
| `LedgerTable` | semantic table inside labeled `.table-viewport` | loading skeleton, empty row, error, corrupt, stale, populated, truncated; sticky header only inside table scroll |
| `EvidenceFeed` | ordered articles | empty/error/stale/populated; chronological order stated; no invented events |
| `CompactChart` | SVG with table alternative and selected datum | empty/unavailable/stale/populated; no current quote without entitlement; keyboard datum focus |
| `StateBadge` | icon plus text | neutral/success/warning/error semantics only; never a decorative dot |
| `BlockerNotice` | blocker code, plain-language effect, observation time, trace control | blocked/unavailable/corrupt; never contains raw exception, payload, or path |
| `TruncationNotice` | count metadata and user-action disclosure | shown whenever `truncated=true`; does not trigger an automatic fetch |
| `CommandComposer` | agent selection, labeled textarea, submit, live result | locked/ready/relay-offline/queued/running/completed/failed/uncertain; one explicit submit, one claim, no paid retry |
| `InteractionTimeline` | ordered immutable receipts | empty/queued/running/completed/failed/uncertain; private only |
| `EvidenceTraceDrawer` | dialog heading, graph/list toggle, close, node detail | open from every value/state, trap focus, Escape close, return focus, route change closes safely |
| `SkeletonBlock` | content-shaped neutral blocks | `aria-hidden`; parent announces loading once; no shimmer or loop |
| `InlineError` | code, effect, recovery condition | direct language; never `window.alert`; no secret-bearing message |
| `PrimitiveShowcase` | production primitives and state fixtures | every displayed number/name is labeled `DEMONSTRATION ONLY`; never reuses production APIs or implies live state |

Interactive primitives have default, hover, pressed, focus-visible, disabled, loading, and applicable
source states. The implementation must render all primitive states at `/showcase` before composing
product workspaces.

### Evidence Trace graph and focus contract

Allowed node kinds are `source_receipt`, `observation`, `dataset`, `code_revision`, `hypothesis`,
`trial`, `reviewer_decision`, `lifecycle_decision`, `paper_receipt`, `process_receipt`,
`deployment_receipt`, and `blocker_terminal`. Every node has a public opaque `node_id`, kind,
bounded label, observed time, safe SHA/reference, state, and source namespace. It never has a local
path or raw payload.

Allowed directed edges are `derived_from`, `observed_by`, `bound_to`, `evaluated_in`,
`reviewed_by`, `decided_by`, `executed_as`, `reconciled_by`, `deployed_as`, and `blocked_by`.
Graphs are acyclic, all referenced nodes exist, and every graph starts at one or more
`source_receipt` nodes. Research/strategy traces terminate at `reviewer_decision` or
`blocker_terminal`; Paper traces terminate at `paper_receipt` or `blocker_terminal`; direct
source/system traces terminate at their typed receipt plus an explicit accepted/blocked decision.
No UI invents an edge to make a graph appear complete.

Opening the drawer stores the invoker, focuses its heading, sets the rest of the application
inert, and announces the selected value. Tab/Shift+Tab stay inside; Escape and the close control
close it; focus returns to the exact invoker if it still exists, otherwise the workspace heading.
Graph node focus follows DOM order and arrow keys; Enter reveals bounded node detail. The list view
is the screen-reader source of truth and preserves source-to-terminal order. Route changes close
the drawer before changing the active main landmark.

## 6. Motion and interaction

| Token | Duration/easing | Use |
| --- | --- | --- |
| `--motion-micro` | `120ms ease-out` | focus/pressed color and opacity |
| `--motion-standard` | `180ms cubic-bezier(0.2, 0, 0, 1)` | route/content opacity transition |
| `--motion-drawer` | `220ms cubic-bezier(0.16, 1, 0.3, 1)` | drawer opacity plus `translateX` |

Only `transform`, `opacity`, and color may transition. Motion must communicate selection, route
change, trace opening, or receipt arrival. No auto-carousel, shimmer, pulsing status, ambient loop,
smooth-scroll override, or motion on a non-interactive decoration. WebSocket events update in
place and announce meaningful state changes politely without stealing focus.

With `prefers-reduced-motion: reduce`, transforms and non-essential transitions are removed; the
drawer appears instantly and focus/semantic state remains complete. Reconnect uses bounded
exponential backoff only after disconnect and is not presented as progress animation.

## 7. Surface, content, and responsive stress

Depth strategy is tonal shift with sparse borders. Only top-level semantic regions may use
`--radius-panel`; rows are separated by tone or a single divider, never wrapped as cards inside
cards. Dense asymmetric layouts align meaningful baselines rather than forcing equal card heights.

Content uses direct Korean-first operator language with stable English identifiers where they are
the evidence. It never uses placeholder success copy, promotional claims, “AI” decoration, or
profitability implications. Empty copy states which authoritative read succeeded. Error copy states
what could not be read and the typed code. Blocked copy states which gate rejected use.

Every primitive is stress-tested with empty content, 40-character Korean/English labels, 160-character
summaries, an unbroken safe SHA, maximum row counts, `truncated=true`, 200% zoom, 375/768/1280px,
keyboard-only input, reduced motion, and reconnect. Safe strings use `overflow-wrap:anywhere`;
primary content never creates page-level horizontal scrolling.

## 8. Accessibility, security, cost, and accepted debt

### Accessibility constraints

- Target WCAG 2.2 AA and zero axe violations or incomplete findings in the tested flows.
- Persistent body contrast is at least 4.5:1; large text and component boundaries at least 3:1.
- Every control is reachable with keyboard, has a visible 2px focus indicator, and uses a native
  element before ARIA. Primary/touch targets are at least 44px; dense desktop secondary controls
  may be 32px only when an equivalent 44px target is available at touch layouts.
- The nine-workspace navigation exposes current location. Main, nav, aside, header, and status
  landmarks have unique names. Live regions announce only state changes, never full snapshot churn.
- Tables have captions and scoped headers. Charts have a data-table/list alternative. CJK,
  text-size, high-contrast, reduced-motion, and screen-reader flows preserve all information.

### Public/private, redaction, mutation, and cost constraints

- `GET /`, `/showcase`, `/api/health`, `/api/snapshot`, and the public viewer WebSocket remain
  keyless and read-only. Public payloads contain no interaction command or response.
- Commands require the existing single-use pairing flow and a `Secure; HttpOnly; SameSite` operator
  cookie. Page JavaScript never receives a long-lived secret. Public submit attempts fail.
- Mac mini projection is redacted before network send. Credentials/tokens, account number or
  fingerprint, raw header/payload/auth response/log line, environment value, absolute/home-relative
  filesystem path, Hermes session ID, local binding key, PID command line, and provider request
  body are prohibited in snapshots, interactions, DOM, logs, artifacts, and Railway storage.
- The public dashboard exposes no order control. KIS, LS, and other providers remain read-only.
  Any Alpaca Paper request remains outside this surface and must pass the exact Paper base URL,
  arm, risk, reconcile, protective OCO, cutoff, and EOD-flat gates.
- Idle behavior is one initial GET, one viewer WebSocket, one publisher WebSocket, and
  `watchfiles`-driven rebuilds. There is no periodic HTTP/DB polling, hidden refresh timer, new
  Railway worker, automatic model call, or paid retry.
- One explicit command submit has one interaction UUID, one durable local claim, and at most one
  Hermes process. Uncertain delivery or process state never launches a replacement.

### Accepted debt

| ID | Item and affected users | Why accepted now | Owner and exit |
| --- | --- | --- | --- |
| `DV2-D01` | Dark-only theme; operators requiring a light theme must use OS/browser contrast tools | Approved workstation direction is a dedicated low-light console; AA and forced-colors remain mandatory | dashboard shell owner; revisit only on explicit user request |
| `DV2-D02` | Compact charts have no audio sonification | Every chart ships an equivalent semantic table/list and keyboard-selected datum, so no information is chart-only | Markets/Derivatives owners; exit if a chart cannot preserve full information in its table |
| `DV2-D03` | V1 remains visible during the compatibility rollout | Required for zero-downtime, separately stored v1 rollback while v2 publisher/server are proven | release owner; close after Todo 13 live v2 proof and v1-ingest removal |

No Critical or Major accessibility/persona issue may be added to this table. New accessibility debt
requires explicit user acknowledgement, affected users, exact location, remediation, and owner.
