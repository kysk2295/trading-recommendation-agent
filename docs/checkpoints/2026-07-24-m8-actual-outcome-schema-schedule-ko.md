# M8 Actual research outcome schema v2 예약 체크포인트

작성 시각: 2026-07-24 09:01 KST

기능 커밋:
`61c5f3970dbf54138935f9dc38880755ca561a84`

## 발견한 운영 결손

2026-07-24와 2026-07-27 actual research job의 frozen executor는 각각
`8ef5904df2589f95cc80013e068d2a2cbdb4c96f`,
`bc400690febe0fb376b68594290a20ea55764b34`였다. 두 SHA 모두 OOS
session-return trace를 보존하는 schema v2 도입 커밋
`303eb8454c3b360cf53504411c332d0648496c3e`보다 앞선다.

기존 plan은 dataset producer와 고정 strategy code version을 분리해 결속했지만
실험 artifact의 required outcome-trace schema는 결속하지 않았다. 그대로 실행하면
clean CSV와 READY foundation이 생겨도 legacy schema v1 trial이 먼저 immutable하게
확정되고, schema v3 terminal audit은
`outcome_trace_schema_v2_required` blocker를 보존할 수밖에 없었다.

실행 전 감사에서 두 날짜 모두 다음 상태였다.

- research·terminal/comparison/overfit receipt와 claim: `0`
- actual research plan과 report: `0`
- 기존 stdout/stderr: 모두 `0 bytes`, mode `600`
- historical trial/review mutation: `0`

## Plan schema v3

새 actual-research plan schema v3는
`required_outcome_trace_schema_version=2`를 immutable spec과 plan ID에 포함한다.
planned CLI는 `--required-outcome-trace-schema-version 2`를 필수로 요구한다.

research loop는 신규 또는 replay experiment artifact의 schema가 plan 요구값과
다르면 독립 Reviewer를 만들기 전에 차단한다. 현재 executor는 schema v2
session-return trace와 bootstrap seed/sample을 그대로 보존한다.

기존 plan schema v2는 새 필드가 없는 원문을 byte-exact로 다시 읽고 재작성하지
않는다. 새 planned execution은 요구값이 없는 legacy plan을 schema v3로 가장하지
않는다.

## TDD와 수동 CLI QA

RED:

- plan spec이 새 required schema 필드를 extra input으로 거부
- CLI help에 required schema 옵션 없음
- 결과: `2 failed`

GREEN:

- actual plan·CLI·loop·terminal audit 관련: `32 passed`
- 전체 pytest: `3591 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- changed-file no-excuse audit: 위반 `0`

actual-shaped CLI:

- `--help`: exit `0`
- unsupported schema `1`: exit `2`
- happy/replay: exit `0/0`
- plan schema: `3/3`
- required outcome trace schema: `2`
- experiment artifact schema: `(2,)`
- plan/artifact mode: `600/600`
- legacy plan schema: `2/2`, byte-exact replay `true`
- external provider, credential, account, broker, order mutation: `0`

첫 수동 fixture는 미래 `registered_at` 때문에 exit `1`로 정상 차단됐다. 이미 종료된
세션과 과거 등록시각으로 시각 계약을 바로잡은 뒤 위 happy/replay를 통과했다.
인과성 게이트는 완화하지 않았다.

## Current-code 실시간 예약

exact clean detached runtime
`/private/tmp/trading-agent-actual-schema-v2-61c5f39`는 기능 커밋 SHA와 정확히 같고
clean status다.

새 job 네 개를 먼저 등록하고 관측한 뒤, 실행 이력이 전혀 없는 기존 pre-v2
research·terminal/comparison/overfit job 여덟 개만 제거했다. 기존 payload와 빈
mode-600 로그는 운영 증거로 보존했다.

| 실행 시각 | label | PID | 결과 계약 |
|---|---|---:|---|
| 2026-07-25 05:18 KST | `ai.trading-agent.post-closeout-research-schema-v2-20260724` | 93323 | strict closeout → causal CSV → READY manifest → 3개 schema v2 trial·Reviewer |
| 2026-07-25 06:50 KST | `ai.trading-agent.actual-research-schema-v3-audit-20260724` | 93325 | plan v3·CSV SHA·foundation·trial/review·comparison·DSR/PBO query-only 감사 |
| 2026-07-28 05:40 KST | `ai.trading-agent.post-closeout-research-schema-v2-20260727` | 93327 | 다음 clean session의 같은 current-code vertical |
| 2026-07-28 06:25 KST | `ai.trading-agent.actual-research-schema-v3-audit-20260727` | 93329 | 다음 session terminal schema v3 감사 |

네 payload/runner는 `zsh -n`, dry-run, bad input exit `2`를 통과했다. payload와
runner mode는 `700`, stdout/stderr는 `600`과 `0 bytes`, receipt/claim은 아직
없다.

기존 upstream strict closeout job
`ai.trading-agent.forward-post-session-20260724-v2`,
`ai.trading-agent.forward-post-session-20260727`은 유지했다. KR 세션과 Hermes도
변경·중단·재시작하지 않았다.

이는 clean session이나 READY·성과·champion 성공 주장이 아니다. 실제 strict
closeout이 실패하면 causal CSV와 trial은 계속 0건으로 차단된다. 성공하면 exact CSV
SHA, READY manifest, schema v2 trial·독립 Reviewer와 terminal schema v3 artifact를
실제 receipt에서 다시 검증한다. Paper 또는 Allocation Manager 권한은 만들지 않는다.
