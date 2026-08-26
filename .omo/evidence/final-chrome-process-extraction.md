# Chrome process lifecycle extraction evidence

Base commit: `894c03c97006d255536b27aae9df4a494ade1a93`

| Success criterion | Exact scenario and invocation | Binary observable | Captured artifact |
| --- | --- | --- | --- |
| Process contracts and launcher extract without controller import breakage | `uv run pytest -q tests/test_local_chrome_controller.py tests/test_local_chrome_controller_lifecycle.py tests/test_local_chrome_controller_process_cleanup.py tests/test_local_chrome_controller_shutdown.py tests/test_local_browser_gateway.py tests/test_local_browser_gateway_cli.py` | Exit `0`; `45 passed in 1.19s`. Controller imports and re-exports `ChromeProcess`, `ChromeLauncher`, and `SubprocessChromeLauncher`. | `.omo/evidence/final-chrome-process-extraction.md` |
| Subprocess launch behavior is unchanged | Same pytest invocation; `test_subprocess_launcher_uses_exact_command_and_safe_popen_flags` patches `trading_agent.local_chrome_process.subprocess.Popen` while constructing `chrome.SubprocessChromeLauncher()`. | Exit `0`; the test proves the exact command, `start_new_session=True`, null streams, `shell=False`, and `umask=0o077`. | `.omo/evidence/final-chrome-process-extraction.md` |
| Owned Chrome signals the exact process group and never directly signals an injected fake | Same pytest invocation; `test_subprocess_launcher_signals_only_its_new_chrome_process_group` patches `trading_agent.local_chrome_process.os.killpg`. | Exit `0`; asserted calls are `(31337, SIGTERM)` then `(31337, SIGKILL)`, with fake `terminate` and `kill` lists empty. | `.omo/evidence/final-chrome-process-extraction.md` |
| Attached Chrome remains unowned | Same pytest invocation; `test_close_leaves_healthy_attached_endpoint` covers an externally supplied healthy endpoint. | Exit `0`; asserted ownership is `attached`, no process id, and no launch or kill. | `.omo/evidence/final-chrome-process-extraction.md` |
| Changed Python files are formatted, linted, and type-checked | `uv run ruff format trading_agent/local_chrome_controller.py trading_agent/local_chrome_process.py tests/test_local_chrome_controller.py tests/test_local_chrome_controller_process_cleanup.py && uv run ruff check trading_agent/local_chrome_controller.py trading_agent/local_chrome_process.py tests/test_local_chrome_controller.py tests/test_local_chrome_controller_process_cleanup.py && uv run basedpyright trading_agent/local_chrome_controller.py trading_agent/local_chrome_process.py tests/test_local_chrome_controller.py tests/test_local_chrome_controller_process_cleanup.py` | Exit `0`; Ruff reports `All checks passed!`; basedpyright reports `0 errors, 0 warnings, 0 notes`. | `.omo/evidence/final-chrome-process-extraction.md` |
| Controller and process modules meet size bounds | `awk '!/^[[:space:]]*$/ && !/^[[:space:]]*(#|//|--)/' trading_agent/local_chrome_controller.py | wc -l` and the same command for `trading_agent/local_chrome_process.py`. | Exit `0`; controller `229` pure LOC, process module `38` pure LOC. | `.omo/evidence/final-chrome-process-extraction.md` |
| Gateway CLI contract remains usable without service calls | Disposable fixture invocation: `uv run python run_local_browser_gateway.py --help`; `uv run python run_local_browser_gateway.py not-a-command`; then `provision` and `verify` with copied `/usr/bin/true` executables and temporary private paths. | Help exit `0`; bad input exit `2`; provision and verify both exit `0` with matching verified JSON and `broker_mutation:0`. No Chrome launch, provider request, or `launchctl` call occurred. | `.omo/evidence/final-chrome-process-extraction.md` |
| Diff has no whitespace errors | `git diff --check` | Exit `0`; no output. | `.omo/evidence/final-chrome-process-extraction.md` |

## Direct verifier run

The following commands were executed directly after commit `7bc3685beeba6dafa25700cf1e94a48f8c10cb0a` from the required worktree. Their output is captured here rather than inferred from a previous report.

```text
$ uv run pytest -q tests/test_local_chrome_controller.py tests/test_local_chrome_controller_lifecycle.py tests/test_local_chrome_controller_process_cleanup.py tests/test_local_chrome_controller_shutdown.py tests/test_local_browser_gateway.py tests/test_local_browser_gateway_cli.py
.............................................                            [100%]
45 passed in 1.33s

$ uv run ruff check trading_agent/local_chrome_controller.py trading_agent/local_chrome_process.py tests/test_local_chrome_controller.py tests/test_local_chrome_controller_process_cleanup.py
All checks passed!

$ uv run basedpyright trading_agent/local_chrome_controller.py trading_agent/local_chrome_process.py tests/test_local_chrome_controller.py tests/test_local_chrome_controller_process_cleanup.py
0 errors, 0 warnings, 0 notes

$ pure-LOC check
controller_pure_loc=229
process_pure_loc=38

$ git diff --check && git show --check --oneline HEAD
7bc3685 refactor: extract owned Chrome process lifecycle
```

Judgment: every required command exited successfully. The focused offline tests exercise re-export compatibility, the fake-process seam, exact process-group signals, and attached-process non-ownership; static gates and both 250-LOC limits pass. The disposable CLI verification documented above did not invoke Chrome, `launchctl`, or a provider.
