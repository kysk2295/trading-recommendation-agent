# G003 Primary source inspection manual QA

All scenarios used deterministic local fixtures under `/private/tmp/g003-primary-qa.wpaHX9`. The CLI made no provider, network, account, position, order, or broker call. Every JSON result reported `broker_mutation: 0`; valid inspections also reported `provider_calls: 0`. No source or config path appeared in CLI output.

## Help

Command:

```text
uv run python run_research_agent_primary_sources.py --help
```

Result: exit `0`; help exposed only the `inspect` command and its required `--config` and deterministic `--now` inputs. No mutation option was present.

## Bad input

Command:

```text
uv run python run_research_agent_primary_sources.py inspect --config /private/tmp/g003-primary-qa.wpaHX9/happy/private/service.json --now not-a-time
```

Result: exit `2`, stdout empty, stderr exactly `{"broker_mutation":0,"status":"invalid"}`.

## Happy current session

Command:

```text
uv run python run_research_agent_primary_sources.py inspect --config /private/tmp/g003-primary-qa.wpaHX9/happy/private/service.json --now 2026-08-03T14:35:00+00:00
```

Result: exit `0`, top-level status `ready`, exactly three ordered families, and source keys `opportunity.us-opportunity-20260803t143400-abcd1234`, `market_context.us-context-20260803t143400`, and `day.session.20260803`. Opportunity and Market Context each exposed one payload provenance digest; Day exposed the payload, paper database, and risk CSV digests.

## Closed session

Command:

```text
uv run python run_research_agent_primary_sources.py inspect --config /private/tmp/g003-primary-qa.wpaHX9/happy/private/service.json --now 2026-08-03T12:00:00+00:00
```

Result: exit `0`, top-level status `blocked`, with `opportunity.blocked.session_closed`, `market_context.blocked.session_closed`, and `day.blocked.session_closed`.

## Stale source

Command:

```text
uv run python run_research_agent_primary_sources.py inspect --config /private/tmp/g003-primary-qa.wpaHX9/stale/private/service.json --now 2026-08-03T14:35:00+00:00
```

Result: exit `0`, top-level status `blocked`; Opportunity reported `opportunity.blocked.stale`, while the current Market Context and Day sources remained independently `ready`.

## Missing spread

Command:

```text
uv run python run_research_agent_primary_sources.py inspect --config /private/tmp/g003-primary-qa.wpaHX9/missing-spread/private/service.json --now 2026-08-03T14:35:00+00:00
```

Result: exit `0`, top-level status `blocked`; Opportunity reported `opportunity.blocked.missing_spread`, while producer-shaped Market Context (which has no spread field) and Day remained independently `ready`.

## Corrective PEP-723 replay

The corrective replay used private local fixtures under `/private/tmp/g003-corrective-qa-20260803`. The breadth producer created Market Context without a spread field; both Day files were mode `600`. No provider, account, position, order, or broker call was made.

```text
uv run --script run_research_agent_primary_sources.py --help
exit 0; inspect, --config, and --now exposed

uv run --script run_research_agent_primary_sources.py inspect --config <private-fixture> --now 2026-08-03T14:35:00+00:00
exit 0; status ready; exactly Opportunity, Market Context, and Day ready; provider_calls 0; broker_mutation 0

uv run --script run_research_agent_primary_sources.py inspect --config <private-fixture> --now not-a-time
exit 2; stderr exactly {"broker_mutation":0,"status":"invalid"}
```

Corrective automated and static gates:

```text
uv run pytest -q tests/test_research_agent_sources.py tests/test_research_agent_primary_admission.py tests/test_research_agent_primary_source_cli.py tests/test_market_context_breadth_producer.py
25 passed in 1.10s

uv run ruff check <eight owned changed Python files>
All checks passed!

uv run ruff format --check <eight owned changed Python files>
8 files already formatted

uv run basedpyright <eight owned changed Python files>
0 errors, 0 warnings, 0 notes

uv run .../check-no-excuse-rules.py <eight owned changed Python files>
no violations in 8 file(s)
```

Corrective production-file line counts were `234, 247, 117, 79`; every file remained below the 250-line ceiling.

## Automated and static gates

```text
uv run pytest -q tests/test_research_agent_sources.py tests/test_research_agent_primary_admission.py tests/test_research_agent_primary_source_cli.py
20 passed in 1.66s

uv run ruff check <eight owned changed Python files>
All checks passed!

uv run basedpyright <eight owned changed Python files>
0 errors, 0 warnings, 0 notes

uv run .../check-no-excuse-rules.py <eight owned changed Python files>
no violations in 8 file(s)
```

Pure LOC counts in owned changed Python files were `215, 183, 72, 55, 143, 186, 98, 72`; every file remained below the 250-line ceiling. The 215-line adapter is in the warning band and should be split before a future material expansion.
