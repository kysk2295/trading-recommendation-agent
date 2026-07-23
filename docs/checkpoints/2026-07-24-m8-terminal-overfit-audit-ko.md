# M8 Actual terminal DSR/PBO audit 체크포인트

작성 시각: 2026-07-24 08:30 KST

기능 커밋:
`56cc4a0ead0f87b919bf5b0474e9878f79240395`

## Forward session 최초 결손 재감사

2026-07-23 실제 US forward 원장은 ranking/watch/retry/candidate input cycle을
각각 355개 보존했다. ranking coverage 2,130건은 모두 `ok`이고 candidate input
cycle도 모두 complete였다.

strict closeout을 막은 최초 결손은 2026-07-24 02:44 KST의 단일 watch cycle이다.

- KIS read-only endpoint: `inquire-time-itemchartprice`
- 최초/최종 상태: `500/500`
- 해당 cycle retry/recovered/failed: `4/3/1`
- watch cycle: exit `1`, `failed`
- 바로 다음 cycle의 같은 종목 read: `500/200`, `recovered`

실패 cycle은 삭제하거나 성공으로 바꾸지 않았다. 이 원인은 이미
`3c476d5390b39c7db252216f2191c6d0d4b8b6fb`에서
`500/502/503/504`에만 `0.25/0.75/2.0초` bounded recovery를 적용해 수정됐고,
오늘 예약된 exact `581eebc08965a647c9b84374e9fade98ccc8a75a`
forward runtime에도 포함된다. 같은 runtime에는 지연된 15:59 closing bar만
2/5초로 다시 읽는 bounded EOD 복구도 포함된다.

이는 오늘 session이 clean하다는 주장이 아니다. 실제 ranking/watch/retry/input과
post-session terminal이 모두 끝난 뒤 strict closeout으로만 확정한다.

## Schema v3 결속

기존 actual terminal audit schema v2는 exact causal dataset, READY foundation,
completed trial, 독립 Reviewer와 equal-risk comparison까지만 결속했다.

schema v3는 strategy cardinality가 정확히 3일 때 다음 두 필드를 필수로 추가한다.

- `overfit_diagnostics_artifact_id`
- `overfit_diagnostics_status`

audit은 같은 experiment ledger, completed experiment 세 개, 독립 review 세 개와
동일 terminal receipt 시각을 `run_intraday_overfit_diagnostics.py` 계약에 전달한다.
진단기는 다시 equal-risk identity를 대사하고 exact OOS session-return trace,
보수적 lane trial count, DSR와 CSCV-PBO 상태를 content-addressed mode-600
artifact로 만든다.

단일/두 전략은 `not_applicable`이다. 기존 schema v2 payload/artifact는 새 필드
없이 schema `2`로 검증되며 무재작성이다.

`collecting`은 terminal audit 실패가 아니다. exact 진단 입력이 아직
20개 동기 session·전략별 30 trades 등의 문턱에 미달했다는 immutable 상태다.
`diagnostic_ready` 역시 lifecycle·주문·allocation 승격 판정이 아니다.

## 수동 CLI QA

actual-shaped 3전략 fixture:

- `--help`: exit `0`
- missing input: exit `1`, blocked report
- first/replay: exit `0/0`
- terminal/comparison/diagnostics artifact: `1/1/1`
- 세 artifact mode: `600`
- equal-risk comparison: `collecting`
- DSR/PBO diagnostics: `collecting`
- external mutation: `0`

검증:

- actual audit·integrity·comparison·diagnostics 타깃: `15 passed`
- 전체 pytest: `3591 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`

## 실시간 query-only 예약

기존 forward, closeout, actual research, schema v2 audit와 comparison job은
변경·중단하지 않았다. exact clean detached runtime
`/private/tmp/trading-agent-actual-audit-v3-56cc4a0`을 추가하고 다음 at-most-once
job 두 건만 등록했다.

| 실행 시각 | label | PID | 입력 data producer | output |
|---|---|---:|---|---|
| 2026-07-25 06:50 KST | `ai.trading-agent.actual-research-overfit-audit-20260724` | 70593 | `8ef5904` | `exact-56cc4a0-schema-v3` |
| 2026-07-28 06:25 KST | `ai.trading-agent.actual-research-overfit-audit-20260727` | 71120 | `bc40069` | `exact-56cc4a0-schema-v3` |

두 runner/payload는 syntax, dry-run, bad-input exit `2`를 통과했다. receipt/claim은
아직 없고 stdout/stderr는 0바이트 mode `600`이다. broker, account, provider,
lifecycle 또는 allocation mutation은 없다.

frozen actual-research runtime이 schema v1 experiment를 만들면 v3 audit은 이를
schema v2로 가장하지 않고 `outcome_trace_schema_v2_required` blocker가 있는
`collecting` 진단으로 보존한다. 새 data version의 actual schema v2 trial은 이후
current-code research runtime으로 별도 실행해야 한다.
