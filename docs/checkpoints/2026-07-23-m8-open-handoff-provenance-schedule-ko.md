# M8 정규장 handoff·provenance 분리·실시간 체인 체크포인트

최초 작성: 2026-07-23 21:56 KST
최종 갱신: 2026-07-23 22:56 KST

## KR 장후 예약 실측

기존 `ai.trading-agent.kr-m3-finalize-20260723`는 2026-07-23 15:32 KST에
실행됐고 마지막 종료 코드는 `0`이었다. terminal, delivery, Reviewer, lifecycle
local control 단계는 모두 성공했지만 성과 세션은 아니었다.

- terminal event: `censored`
- reason: `no_shadow_entry_artifact`
- completed/censored/failed sessions: `0/1/0`
- completed trades: `0`
- automatic champion, order authority, allocation change: 모두 `false`

21:41 KST 관측에서 launchd run count가 2초 사이 `2096 → 2097`로 증가했다.
기존 job과 process를 변경·중단·재시작하지 말라는 운영 제약에 따라 손대지 않았다.

## 7월 23일 US 장전 strict readiness 실측

exact runtime `3d488137ce6d612ebea98dd0b862e1fe9843ef44`의 at-most-once job이
22:25 KST에 실행돼 atomic receipt `exit_code=0`과 `ready` report를 남겼다.

- combined input SHA-256:
  `2c76600c82c525ac24980807753ad1538ff097f4cb2a98b11c6a933b1cf2c441`
- premarket cycles / ranking requests: `65 / 390`
- ranking snapshot rows: `29,184`
- latest observed age / selected candidates: `139 seconds / 10`
- quality gate relaxed: `false`
- provider/account/order mutation: `0`
- receipt/report/stdout/stderr mode: `600`
- 종료 뒤 launchd label: 없음

이는 장전 artifact completeness의 실제 성공 증거이며 정규장 ranking/watch/retry,
candidate cycle, post-session terminal 또는 clean forward session의 성공 증거는 아니다.

## 정규장 handoff 결손 수정

premarket collector가 provider operation 소요시간과 무관하게 항상 300초를 더
기다리면 마지막 장전 cycle이 09:27 EDT에 시작한 경우 첫 정규장 scan이 09:32까지
밀릴 수 있었다. commit
`415ce3ea9567bac73ba4239e352e86c32ab05c19`에서 operation 종료 뒤 clock을 다시
읽고 다음 sleep을 official regular open으로 상한 처리했다.

- 후보와 실패 cycle은 삭제하지 않았다.
- ranking/watch/retry/post-session 품질 gate는 완화하지 않았다.
- 현재 실행 중인 7월 23일·24일 frozen runtime에는 코드를 주입하지 않았다.

## 정규장 cycle cadence 결손 수정

7월 23일 실제 watch의 첫 정규장 cycle은 22:32:41 KST, 두 번째는 22:33:58
KST에 시작해 간격이 `76.7초`였다. 이후 cycle도 scan operation 소요시간
약 5~17초와 고정 60초 sleep이 합쳐져 시작 간격이 약 65~77초였다. 따라서
`--cycles 390 --interval-seconds 60`을 지정해도 정규장 안에 390개 start cadence를
실행할 수 없었다.

commit `5ecad89ba8eb68319c7fce95290103a1ad83bc69`에서 `run_cycles`가 monotonic
clock으로 operation runtime을 측정하고 다음 sleep에서 차감하도록 수정했다. operation
overrun은 음수 sleep 없이 즉시 다음 cycle로 진행하고 process는 계속 직렬 실행한다.
현재 실행 중인 7월 23일·24일 frozen watch에는 코드를 주입하거나 재시작하지 않았다.

검증 결과:

- 실제 RED 근거: 첫 두 cycle 시작 간격 `76.7초`
- 17초 operation 재현: 기존 60초 sleep에서 수정 뒤 `43초` sleep
- focused watch tests: `36 passed`
- CLI `--help`, cycles `0` exit `2`, 격리 2-cycle happy path: pass
- 격리 happy start interval: `1.003초` / 설정 `1초`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: `3467 passed`
- 기존 Grok offline environment 테스트: 변경과 무관한 `5 failed`
- no-excuse 검사: pass

22:45 early progress original job은 제한된 launchd `PATH`에서 shebang의 `uv`를 찾지
못해 `exit_code=127`로 실패했다. 실패 receipt는 보존했다. 명시적
`/Users/goyunseo/.local/bin/uv` command의 새 label로 22:46에 재검증한 결과는
`progress_clean`, ranking/watch/retry/candidate cycle `12/12/12/12`, KIS
retry/recovery/repeated failure `36/36/0`, gate relaxed `false`, external mutation
`0`이었다. retry receipt와 report는 mode `600`이고 종료 뒤 label은 제거됐다.

commit `3d76f891343503593a476c3ea80060cf82f0ff06`에서 같은 delayed failure를 공용
one-shot scheduler의 fail-fast 계약으로 닫았다. `/usr/bin/env` shebang command는
wrapper, log, receipt 생성 전에 `explicit_interpreter_required`로 차단되고 명시적
`/bin/zsh` 또는 absolute `uv` command만 예약한다. unsafe CLI는 exit `1`, artifact
mutation `0`이었고 explicit `uv --version` wrapper는 exit `0`, receipt mode
`600`이었다. focused `3 passed`, 전체 `3468 passed`, Ruff와 basedpyright가
통과했으며 기존 Grok offline environment 테스트만 동일하게 `5 failed`였다.

22:55 KST 추가 live audit은 ranking/watch/retry/candidate cycle `21/21/21/21`,
KIS retry/recovery `67/67`, `progress_clean`, final eligibility
`pending_post_session`이었다. 7월 27일 일곱 runner의 최종 executable도 `/bin/zsh`
또는 명시적 `/Users/goyunseo/.local/bin/uv`로 새 계약을 모두 통과했다.

## dataset producer와 전략 버전 분리

actual coordinator는 기존에 `code_version` 하나를 dataset materializer의
`producer_commit_sha`와 immutable strategy manifest의 code version에 동시에
사용했다. 새 materializer SHA를 쓰면 기존 v1 전략 정체성이 drift하고, 기존 전략
SHA를 쓰면 dataset producer provenance가 거짓이 되는 결손이었다.

commit `f917ffeff52f8ab02cf40f8978e071a1c0ca073a`에서 두 정체성을 분리했다.

- `--dataset-producer-commit-sha`: exact lowercase 40자리 materializer Git SHA
- `--code-version`: frozen strategy code identity
- actual request, immutable run spec, plan, catalog와 두 CLI가 두 필드를 독립 전달
- 새 필수가 immutable plan payload에 들어가므로 plan/content schema를 `2`로 승격
- closeout 선행조건 오류는 typed `CloseoutPrerequisiteError`로 보존

수동 planned CLI happy path에서 dataset receipt는
`dddddddddddddddddddddddddddddddddddddddd`, strategy manifest는
`eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee`을 각각 보존했다. 잘못된 producer
SHA는 exit `1`, `blocked`, external mutation `0`이었다.

검증 결과:

- focused: `15 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: `3462 passed`
- 기존 Grok offline environment 테스트: 변경과 무관한 `5 failed`
- no-excuse 검사: pass
- 변경한 모든 Python 파일: 순수 코드 250줄 이하

## exact persisted manifest terminal audit

실제 coordinator-shaped 실행을 새 audit 경계로 검사하는 과정에서 input binding
receipt와 manifest 파일명은 canonical JSON 뒤 newline까지 포함한 저장 bytes SHA를
사용하지만, walk-forward experiment artifact는 newline이 없는 model JSON SHA를
사용하는 provenance 결손을 발견했다. 따라서 기존 trial은 내용이 같은 manifest를
가리키더라도 exact persisted artifact SHA에는 결속되지 않았다.

commit `20d81964b3200d4c16253409d58306333c9457be`에서 actual coordinator가 input
binding의 `manifest_sha256`을 research loop에 명시적으로 전달하도록 수정했다.
in-memory library 호출은 기존 canonical SHA를 유지하고, 외부에서 전달된 값은
정확한 64자리 lowercase SHA만 허용한다.

같은 commit의 query-only terminal audit은 다음 evidence를 독립적으로 다시 읽고
서로 대조한다.

- immutable run plan과 frozen dataset producer/strategy code identity
- 성공한 one-shot research receipt와 exact `ready` report
- catalog, causal CSV, dataset receipt의 content SHA와 session cardinality
- input-binding receipt, exact persisted v2 manifest SHA와 manifest 순서의 READY
  foundation 1~3개
- global experiment ledger의 전략별 단일 completed trial
- exact walk-forward artifact와 독립 Reviewer artifact/decision

audit 결과 자체도 content-addressed mode-600 JSON과 한국어 report로 발행한다.
automatic state, order authority, allocation change는 모두 `false`이고 provider,
credential, account, order mutation 경로가 없다.

검증 결과:

- focused: `29 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: `3466 passed`
- 기존 Grok offline environment 테스트: 변경과 무관한 `5 failed`
- no-excuse 검사: pass
- CLI `--help`, 잘못된 producer SHA 차단, 실제 coordinator-shaped happy path: pass
- 3전략 foundation 순서 `VWAP → HOD → Gap` 보존: pass

## 2026-07-27 exact-runtime 실시간 체인

readiness와 closeout은 clean detached runtime
`/private/tmp/trading-agent-open-handoff-20260727-f917ffe`의 exact commit
`f917ffeff52f8ab02cf40f8978e071a1c0ca073a`를 유지한다. forward, early/late
progress, research와 terminal audit은 clean detached runtime
`/private/tmp/trading-agent-cycle-cadence-20260727-5ecad89`의 exact commit
`5ecad89ba8eb68319c7fce95290103a1ad83bc69`로 교체했다. forward, research와
terminal audit payload는 실행 직전 runtime HEAD, dirty 상태와 뉴욕 거래일을
검사한다. progress runner는 명시적 `uv --directory`와 exact script 절대경로를
사용한다.

| 실행 시각 | launchd label | 역할 | 등록 PID |
|---|---|---|---:|
| 2026-07-27 16:59:30 KST / 03:59:30 EDT | `ai.trading-agent.us-forward-open-handoff-20260727` | 장전 5분 cycle에서 09:30 정규장으로 handoff한 뒤 start-to-start 1분 cadence의 390 cycle, retry, candidate, post-session chain 수집 | 75778 |
| 2026-07-27 22:25 KST / 09:25 EDT | `ai.trading-agent.forward-premarket-readiness-20260727` | 최소 60개 장전 cycle, 최신 600초, 최신 후보 1개 이상 strict readiness | 42505 |
| 2026-07-27 22:45 KST / 09:45 EDT | `ai.trading-agent.forward-progress-early-20260727` | 최소 8 cycle ranking/watch/retry/candidate strict progress | 77271 |
| 2026-07-28 04:30 KST / 2026-07-27 15:30 EDT | `ai.trading-agent.forward-progress-late-20260727` | 최소 300 cycle strict progress와 미복구 retry/coverage 결손 조기 차단 | 77277 |
| 2026-07-28 05:20 KST / 2026-07-27 16:20 EDT | `ai.trading-agent.forward-post-session-20260727` | watch terminal을 최대 16:35 EDT까지 기다린 뒤 strict local closeout | 42511 |
| 2026-07-28 05:40 KST / 2026-07-27 16:40 EDT | `ai.trading-agent.post-closeout-research-20260727` | closeout receipt/report를 요구한 뒤 actual causal dataset, READY foundation, multi-strategy walk-forward와 독립 Reviewer 실행 | 75784 |
| 2026-07-28 05:50 KST / 2026-07-27 16:50 EDT | `ai.trading-agent.actual-research-terminal-audit-20260727` | research receipt를 최대 17:05 EDT까지 기다린 뒤 exact persisted manifest, 1~3 READY foundation, completed trials와 독립 Reviewer terminal 재검증 | 75789 |

research run은 다음 identity를 동결한다.

- run key: `actual-2026-07-27`
- dataset producer:
  `5ecad89ba8eb68319c7fce95290103a1ad83bc69`
- frozen strategy code:
  `70e7d94dd0f56bc40b9fe602de22657c38f8e844`
- strategy bindings: 기존 v1 VWAP reclaim, HOD breakout, Gap-and-Go와 exact queue
  card SHA
- required current session: `2026-07-27`
- minimum clean sessions: `1`

등록 직후 일곱 job은 모두 `state=running`, `runs=1`, receipt/claim은 없었다.
payload와 at-most-once wrapper는 mode `700`, stdout/stderr는 mode `600`이며 모든
shell syntax 검사가 통과했다. 기존 7월 23일·24일 jobs와 Hermes PID `31663`은
그대로 살아 있었고 원본 dirty checkout은 수정하지 않았다.

이 예약은 아직 clean session, READY dataset 또는 성과 증거가 아니다. 각 실행 뒤
atomic receipt, strict report, exact CSV SHA, foundation/manifest SHA, trial과 Reviewer
terminal을 다시 검증해야 한다. `launchctl submit` 작업이므로 해당 GUI login
session과 머신이 실행 시각까지 유지되어야 한다.

executable Paper champion이 두 개 미만이므로 Alpaca Paper arm job은 만들지 않았고
Allocation Manager도 활성화하지 않았다. 실제 자금 거래 권한은 없다.
