# M8 정규장 handoff·provenance 분리·실시간 체인 체크포인트

작성 시각: 2026-07-23 22:08 KST

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

## 정규장 handoff 결손 수정

premarket collector가 provider operation 소요시간과 무관하게 항상 300초를 더
기다리면 마지막 장전 cycle이 09:27 EDT에 시작한 경우 첫 정규장 scan이 09:32까지
밀릴 수 있었다. commit
`415ce3ea9567bac73ba4239e352e86c32ab05c19`에서 operation 종료 뒤 clock을 다시
읽고 다음 sleep을 official regular open으로 상한 처리했다.

- 후보와 실패 cycle은 삭제하지 않았다.
- ranking/watch/retry/post-session 품질 gate는 완화하지 않았다.
- 현재 실행 중인 7월 23일·24일 frozen runtime에는 코드를 주입하지 않았다.

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

## 2026-07-27 exact-runtime 실시간 체인

clean detached runtime
`/private/tmp/trading-agent-open-handoff-20260727-f917ffe`를 exact commit
`f917ffeff52f8ab02cf40f8978e071a1c0ca073a`에 고정했다. 네 payload는 실행 직전
runtime HEAD, dirty 상태와 뉴욕 거래일을 다시 검사한다.

| 실행 시각 | launchd label | 역할 | 등록 PID |
|---|---|---|---:|
| 2026-07-27 16:59:30 KST / 03:59:30 EDT | `ai.trading-agent.us-forward-open-handoff-20260727` | 장전 5분 cycle에서 09:30 정규장으로 handoff한 뒤 390개 1분 cycle, retry, candidate, post-session chain 수집 | 42499 |
| 2026-07-27 22:25 KST / 09:25 EDT | `ai.trading-agent.forward-premarket-readiness-20260727` | 최소 60개 장전 cycle, 최신 600초, 최신 후보 1개 이상 strict readiness | 42505 |
| 2026-07-28 05:20 KST / 2026-07-27 16:20 EDT | `ai.trading-agent.forward-post-session-20260727` | watch terminal을 최대 16:35 EDT까지 기다린 뒤 strict local closeout | 42511 |
| 2026-07-28 05:40 KST / 2026-07-27 16:40 EDT | `ai.trading-agent.post-closeout-research-20260727` | closeout receipt/report를 요구한 뒤 actual causal dataset, READY foundation, multi-strategy walk-forward와 독립 Reviewer 실행 | 42516 |

research run은 다음 identity를 동결한다.

- run key: `actual-2026-07-27`
- dataset producer:
  `f917ffeff52f8ab02cf40f8978e071a1c0ca073a`
- frozen strategy code:
  `70e7d94dd0f56bc40b9fe602de22657c38f8e844`
- strategy bindings: 기존 v1 VWAP reclaim, HOD breakout, Gap-and-Go와 exact queue
  card SHA
- required current session: `2026-07-27`
- minimum clean sessions: `1`

등록 직후 네 job은 모두 `state=running`, `runs=1`, receipt/claim은 없었다.
payload와 at-most-once wrapper는 mode `700`, stdout/stderr는 mode `600`이며 모든
shell syntax 검사가 통과했다. 기존 7월 23일·24일 jobs와 Hermes PID `31663`은
그대로 살아 있었고 원본 dirty checkout은 수정하지 않았다.

이 예약은 아직 clean session, READY dataset 또는 성과 증거가 아니다. 각 실행 뒤
atomic receipt, strict report, exact CSV SHA, foundation/manifest SHA, trial과 Reviewer
terminal을 다시 검증해야 한다. `launchctl submit` 작업이므로 해당 GUI login
session과 머신이 실행 시각까지 유지되어야 한다.

executable Paper champion이 두 개 미만이므로 Alpaca Paper arm job은 만들지 않았고
Allocation Manager도 활성화하지 않았다. 실제 자금 거래 권한은 없다.
