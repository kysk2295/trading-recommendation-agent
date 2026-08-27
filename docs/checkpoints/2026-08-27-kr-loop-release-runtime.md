# KR Loop Engineer Release Runtime Checkpoint

Date: 2026-08-27 (Asia/Seoul)

## Result

The KR Loop Engineer lifecycle now has an executable deployment path instead of a ledger-only `promoted` state.

- A successful bounded Grok mutation retains two private source releases: the unchanged baseline and the verified candidate.
- Every launch re-verifies the selected release commit and tracked-source digest. A modified release is rejected before the Research Agent process executes.
- The active-release manifest is atomically replaceable and is the single code-selection authority for the persistent Research Agent.
- The release reconciler projects append-only `promote` and `rollback` events into that manifest and restarts the existing Research Agent label. A failed restart restores the previous manifest.
- Champion and challenger Research Agent cycles run sequentially in separate state roots. A shadow receipt is created only from same-session `KrAutonomousOutcomeMemory` records produced by both lanes.
- Two qualifying future Korean sessions remain required for promotion.
- Post-promotion monitoring reads the persisted Research Agent health report and KR virtual outcome memories. A stale/failed runtime, data-eligibility failure, virtual position mismatch, or research-task loss records a rollback and switches execution to the retained baseline.
- The macOS Loop LaunchAgent runs at 16:30 and 18:30 on weekdays. The official KIS calendar and the 15:40 post-close boundary must still confirm a completed Korean session before work starts.
- All new paths remain research/paper-only and expose no broker mutation authority. No Alpaca or KIS/LS trading endpoint was added or changed.

## Operator surfaces

```text
uv run --offline python run_kr_loop_automation.py provision --config <loop-config>
uv run --offline python run_kr_loop_automation.py verify --config <loop-config>
uv run --offline python run_kr_loop_automation.py status --config <loop-config>
uv run --offline python run_kr_loop_automation.py tick --config <loop-config>
uv run --offline python run_kr_loop_automation.py install \
  --config <loop-config> \
  --current-research-plist <currently-loaded-research-plist>
```

`install` replaces the Research Agent job only after contract verification. It waits for a fresh matching service-health report before installing the Loop job and restores the previous Research Agent plist on failure.

## Verification evidence

- Focused and adjacent regression suite: 65 tests passed before the final integration additions.
- Full two-session fixture: isolated champion/challenger outcome memories produced `shadowed`, then `promoted`, changed the active manifest to `candidate`, and invoked one Research Agent restart.
- Stale-health fixture: recorded `rolled_back`, changed the active manifest to `baseline`, and invoked the second restart.
- Ruff: changed Python files passed.
- basedpyright: changed production Python files passed with zero errors and warnings.
- Manual CLI gate: help rendered; a missing config returned exit 2 with the redacted error; valid fixture `status` and closed-session `tick` returned canonical JSON.

The fixture and synthetic scores prove lifecycle behavior only. They do not prove profitability or a natural-session market outcome.
