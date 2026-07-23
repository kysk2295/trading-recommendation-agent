# US forward strict post-session closeout 체크포인트

## 실제 결손과 현재 producer 상태

과거 네 forward session의 ranking/watch/candidate/retry 결손은 KIS server 반복 실패,
scanner child dependency 누락과 중간 process 종료에서 발생했다. dependency preflight와
bounded 3회 server retry는 기존 checkpoint에서 이미 수정됐고 실패 cycle은 그대로
보존돼 있다.

2026-07-23 20:52 KST actual producer는 장전 cycle `47`, ranking request
`282=47×6`, 실패 `0`을 보존하고 있었다. 따라서 새 ranking failure를 추정하지 않았다.
남은 구조적 결손은 post-session metrics·daily research·adaptive evaluation이 장전부터
6시간 이상 살아 있는 watch process 하나에만 연결돼, clean regular cycle 뒤 process가
중단되면 독립적으로 닫을 경계가 없다는 점이다.

## 구현 계약

`run_forward_post_session.py`는 provider와 account를 열지 않는 local-only closeout이다.

- exact session date의 공식 정규장 종료 뒤에만 신규 closeout을 허용한다.
- `kis_ranking_request_coverage.csv`, `watch_cycles.csv`,
  `kis_read_retry_cycles.csv`, `candidate_input_cycles.csv`와 candidate SQLite를 기존
  strict progress loader로 먼저 감사한다.
- watch timestamp는 모두 해당 뉴욕 거래일 정규장 안에 있어야 한다.
- watch/ranking/retry/candidate cycle 실패·결손·cardinality 불일치가 하나라도 있으면
  post artifact를 만들지 않는다.
- 기존 post 실패행은 `post_session_failure_preserved`로 닫고 재시도하지 않는다.
- post 성공행은 정확히 하나만 허용하며 과거 session은 query-only replay만 가능하다.
- current session에 post 행이 전혀 없을 때만 recommendation finalization과 기존
  metrics→daily research→adaptive evaluation chain을 실행한다.
- recovery runner가 실패행을 남기면 다음 실행도 이를 보존하고 차단한다.
- 성공 뒤 필수 metrics artifact checksum, 20bp metric, strict replay source와 모든
  candidate symbol의 390분 causal coverage를 다시 검증한다.
- aggregate report는 mode `600`이며 실패 cycle 삭제, quality gate 완화,
  provider·credential·account·order operation은 모두 `0`이다.

## TDD와 CLI QA

module 부재 import error를 RED로 확인한 뒤 다음 경계를 GREEN으로 만들었다.

- clean session의 missing post chain은 한 번만 복구되고 다음 실행은 replay
- failed watch는 finalizer와 runner 호출 `0`
- 기존 post failure는 보존되고 retry `0`
- runner failure는 non-retryable terminal이 되어 다음 실행도 차단
- 정규장 종료 전 mutation `0`
- historical success는 query-only replay, historical missing terminal은 생성 금지

검증 결과:

- closeout 집중 테스트: `9 passed`
- watch·ORB trial·lane·challenger·daily record 관련: `40 passed`
- 전체 pytest: `3458 passed in 205.01s`
- 전체 Ruff: pass
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit `0`
- invalid date: exit `2`, output 미생성
- 완결 historical fixture happy/replay: exit `0/0`, 둘 다 `replayed`
- post terminal CSV line: `2 → 2`, 신규 행 `0`
- aggregate report mode: `600`

## actual 장후 예약

구현 SHA의 clean detached runtime
`/private/tmp/trading-agent-forward-closeout-20260723-a945ba4`를 만들고 공용
at-most-once runner로 예약했다.

- label: `ai.trading-agent.forward-post-session-20260723`
- 시작: 2026-07-23 15:55 EDT / 2026-07-24 04:55 KST
- close gate: 16:00 EDT
- watch terminal deadline: 16:15 EDT
- session: `outputs/live_sessions/20260723`
- output:
  `outputs/live_sessions/20260723/post_session_closeout/exact-a945ba4`
- 등록 직후 state: running, run count `1`, PID `7396`
- payload/wrapper mode: `700`
- stdout/stderr mode: `600`
- receipt: pending

payload는 watch가 post success를 먼저 만들면 exact replay만 수행한다. watch label이
사라졌는데 post terminal이 없을 때만 strict recovery를 실행한다. 기존 watch,
dataset, research와 Hermes process는 변경·중단·재시작하지 않았다.

예약은 actual clean session 성공 증거가 아니다. 장후 receipt, closeout result,
cycle cardinality, causal coverage와 이어지는 dataset·foundation·trial·Reviewer를
실제 산출물로 다시 검증해야 한다. promotion threshold, Paper arm, allocation authority는
변경하지 않았다.
