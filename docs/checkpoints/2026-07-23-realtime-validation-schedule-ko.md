# 2026-07-23 실시간 검증 예약 체크포인트

## 예약 결과

clean commit `0c7dc575301862d3cf0d98c6d9c16c69111783fb`의 detached runtime을 사용해
현재 US forward watch 뒤에 필요한 실제 장중·장마감 검증을 one-shot launchd job으로
연결했다. 기존 KR finalizer, Hermes delivery service와 US forward watch는 변경하거나
재시작하지 않았다.

| launchd label | 실행 조건 | 검증 |
|---|---|---|
| `ai.trading-agent.us-forward-20260723` | 장전부터 정규장 종료까지 | KIS ranking, candidate/watch, EOD, metrics, daily research, adaptive evaluation |
| `ai.trading-agent.us-day-preflight-20260723` | 09:30~15:30 EDT, watch DB 변경 시 | current completed bar와 setup, Alpaca Paper GET/WSS readiness, broker/shadow reconciliation |
| `ai.trading-agent.alpaca-sip-smoke-20260723` | 09:35 EDT | AAPL SIP trade stream 최대 3 frame read-only smoke |
| `ai.trading-agent.us-day-finalizer-20260723` | watch 종료 뒤, 최대 16:15 EDT | flat broker 상태, reconciliation, real scheduled-session terminal |
| `ai.trading-agent.intraday-dataset-20260723` | watch 종료 뒤, 최대 16:30 EDT | strict quality gate를 통한 causal research CSV materialization |
| `ai.trading-agent.intraday-research-20260723` | dataset 종료 뒤, 최대 17:00 EDT | exact CSV·receipt의 세 READY foundation/v2 manifest 결속과 실제 walk-forward·독립 Reviewer |

KR의 당일 data-quality censored trial은 기존
`ai.trading-agent.kr-m3-finalize-20260723`가 15:32 KST에 terminal, 독립 Reviewer와
lifecycle evidence로 닫는다.

15:36 KST 장 시작 전 점검에서 7월 23일 forward job이 KIS 반복 server-error 복구 전
runtime `0c7dc575`를 사용하고 있음을 발견했다. 아직 watch database를 만들기 전인
대기 상태에서 downstream finalizer와 dataset watcher만 잠시 suspend하고 동일 label을
retry 복구 runtime `d59d2534a2561472c894bfe2acb56bd051dfca90`로 교체한 뒤 즉시
resume했다. downstream PID와 run count는 유지됐고 새 forward runner는 mode 700,
stdout/stderr는 mode 600, broker mutation은 false다. KR finalizer와 Hermes service는
이 교체 대상이 아니었다.

같은 날 dataset 뒤 수동 단절도 남기지 않았다. 별도 actual research job은
`26b5e2538c354837c27d827a08f45ba5cdf2a45c` frozen runtime에서 dataset READY와
artifact cardinality를 먼저 확인하고, KIS entitlement와 exact 세 queue card가 모두
맞을 때만 binding과 Reviewer를 실행한다. runner `zsh -n`, dry-run, bad input과
mode 700, launchd run count 1·running, stdout/stderr mode 600을 확인했다.

## 권한 경계

- SIP smoke는 `--arm-read-only`만 사용하고 계좌·주문 endpoint를 호출하지 않는다.
- US Day observer와 finalizer는 Paper account/order state를 GET/WSS로 대사하지만
  `PaperMutationArm`을 소비하지 않고 POST/DELETE를 수행하지 않는다.
- signed Hermes arm 설정과 confirmed one-time request가 없으므로 실제 Paper mutation
  job은 예약하지 않았다. scheduler가 arm을 생성하거나 확인을 대행하지 않는다.
- KR four-source 다음 세션은 OpenDART private 설정이 없어서 complete source cycle을
  만들 수 없다. 품질 gate를 완화하거나 source 실패를 성공으로 바꾸지 않는다.

## 배치 전 검증

- 네 신규 wrapper `zsh -n`: 모두 exit `0`
- 네 신규 wrapper `--dry-run`: 모두 exit `0`
- 네 신규 wrapper unknown argument: 모두 exit `2`
- wrapper mode: 모두 `700`
- frozen runtime의 preflight, SIP smoke, dataset CLI help: 모두 exit `0`
- 신규 launchd job: 모두 `running`, run count `1`, terminal exit 미발생
- 기존 KR finalizer, Hermes delivery service, US forward watch: 기존 PID 유지

실제 장중 결과는 각 private mode-600 stdout/stderr/event artifact와 terminal/dataset
artifact로 판정한다. 예약 상태 자체는 readiness, clean session, Paper 실행 또는
성과 증거가 아니다.

## 2026-07-24 연속 실행 확장

7월 23일 표본이 실패하더라도 다음 정규장 표본을 자동 확보하도록 7월 24일에도
US forward, 장중 preflight, read-only SIP smoke, 장마감 finalizer, strict causal
dataset job을 같은 fail-closed 경계로 예약했다. forward runtime은 retry 복구가
포함된 `d59d2534a2561472c894bfe2acb56bd051dfca90`에 고정되어 있다.

dataset 뒤의 수동 단절도 제거했다. 별도
`ai.trading-agent.intraday-research-20260724` one-shot은 dataset job이 terminal이
될 때까지 기다린 뒤 다음을 순서대로 실행한다.

1. dataset report가 `ready`이고 exact CSV/receipt가 각각 하나인지 확인한다.
2. `26b5e2538c354837c27d827a08f45ba5cdf2a45c` frozen runtime, mode-600 KIS
   historical-research entitlement, exact 세 queue card로 VWAP reclaim, HOD
   breakout, Gap-and-Go READY foundation과 v2 manifest를 결속한다.
3. 같은 CSV SHA, foundation SHA, queue snapshot을 사용해 bounded multi-strategy
   walk-forward와 query-only 독립 Reviewer를 실행한다.

dataset이 blocked이거나 artifact cardinality, runtime SHA, entitlement, queue,
foundation 중 하나라도 다르면 후속 loop는 실행하지 않는다. runner `zsh -n`,
`--dry-run` exit 0, unknown argument exit 2, mode 700을 확인했고 launchd의 run
count 1·running 상태와 stdout/stderr mode 600을 확인했다. 이 예약은 주문 권한,
lifecycle 자동 승격 또는 성과 증거가 아니다.

이후 7월 24일 dataset job을 strict 누적 catalog runtime
`322d90498e6e3fcdd6fd5e6952f0d5f3e6912c1b`로 교체했다. 7월 23일과 24일
session directory를 모두 감사하지만 `required_session_date=2026-07-24`라서 당일
세션 자체가 clean selection에 없으면 과거 표본만으로 dataset을 재발행하지 않는다.
교체 중 후속 research watcher만 suspend/resume해 label 공백을 관측하지 않게 했고,
후속 PID·run count는 유지됐다. catalog runner `zsh -n`, dry-run, bad input, mode
700과 launchd run count 1·running, 외부 로그 mode 600을 확인했다.

## actual research immutable run plan 적용

두 날짜의 research watcher를 frozen runtime
`e095bef9cf3d90dd38ec6f31d1fc8009b3f92a4f`의
`run_planned_intraday_actual_research.py`로 교체했다.

- 7월 23일 run key: `actual-2026-07-23`
- 7월 24일 run key: `actual-2026-07-24`
- 두 날짜의 strategy version:
  - `actual_vwap_reclaim_forward_v1`
  - `actual_hod_breakout_forward_v1`
  - `actual_gap_and_go_forward_v1`
- 7월 24일 plan은 7월 23일과 24일 session directory를 함께 감사하고
  `required_session_date=2026-07-24`를 요구한다.
- 최초 실행 시점의 최신 exact queue를 날짜별 immutable plan에 고정하고 같은 job
  재시작은 그 plan을 재사용한다.

교체 전후 KR finalizer는 terminal 대기 상태, Hermes PID는 `31663`으로 유지됐다.
forward, dataset, preflight, SIP smoke와 day finalizer job은 변경하지 않았다. 새
research PID는 `18088`, `18094`, run count `1`, state `running`이며 dataset job
종료를 기다리고 있다. runner `zsh -n`, dry-run, bad input, mode `700`, frozen
runtime clean SHA와 stdout/stderr mode `600`을 검증했다. 아직 plan file이 없는 것은
dataset READY 전 queue를 조기에 고정하지 않는 의도한 상태다.

## 7월 24일 current-schema lane 증거 연결

원본 checkout의 Paper execution DB와 global experiment ledger는 query-only로
감사했다. execution DB는 current schema, account-bound, intent/unresolved `0/0`이지만
global experiment ledger는 schema v6이고 Hermes arm DB가 없었다. 원본을 수정하지
않고 integration worktree에 SQLite backup을 만들고 experiment ledger 사본만
v6→v7로 migration했다. migration 사본의 intraday 전략은 모두
`experimental_shadow`이고 `PAPER_CHAMPION`은 0개라서 arm gateway는 의도대로
`champion_missing`을 유지한다.

7월 24일에는 d59 runtime의 네 전략 계약을 별도 current-schema experiment ledger에
사전등록했다. ORB를 포함한 strategy version은
`d59d2534a2561472c894bfe2acb56bd051dfca90`에 결속되고 effective session date는
`2026-07-24`, lifecycle state는 `experimental_shadow`다. integration lane
registry에는 account fingerprint를 출력하지 않고 기존 current-schema execution
사본의 account binding만 추가했다.

7월 24일 forward watcher는 다음 local-only 증거 경계를 함께 사용하도록 시작 전에
교체했다.

- integration Paper execution 사본
- current lane registry와 신규 lane review ledger
- d59 code-bound current-schema experiment ledger
- lane forward-validation output

교체 동안 7월 24일 preflight, SIP, finalizer, dataset, research watcher만 잠시
suspend/resume했고 모든 PID를 유지했다. forward label은 새 PID `29095`, run count
`1`, state `running`이며 기존 Hermes PID `31663`은 유지됐다. forward stdout/stderr의
기존 mode `644`도 내용 변경 없이 `600`으로 보정했다. runner `zsh -n`, dry-run,
bad input과 다섯 lane/experiment 인자를 검증했다. 이 예약은 Paper 주문이나 champion
승격을 수행하지 않으며 실제 7월 24일 clean session의 snapshot·Reviewer·trial
terminal을 그대로 보존한다.

## Paper smoke 사전 자격 감사 예약

commit `0052a6bd1ec37712ab795a7330be53a5c1c32d6b`에 local-only
`run_us_day_paper_smoke_eligibility.py`를 추가했다. exact session/lane에 대해 clean
repository commit, 단일 `PAPER_CHAMPION`, Paper authority binding, lane risk/account
binding, current execution schema와 account binding 일치, unresolved intent, pending
trade-update receipt와 미복구 quarantine 부재를 확인한다. 성공해도
`ready_to_request_arm`만 기록하며 arm을 생성·확인·소비하지 않는다.

동일 commit의 clean detached runtime을 사용한 실제 integration control-store 감사
결과는 `champion_missing`이었다. 보고서는 mode `600`이고 provider/account/order
mutation은 `0`이다. 이 결과는 현재 네 전략이 모두 `experimental_shadow`라는 기존
원장 상태와 일치하며, 자동 승격이나 가짜 champion을 만들지 않았다.

`ai.trading-agent.paper-smoke-eligibility-20260724` one-shot을 7월 24일
09:31 EDT에 실행하도록 등록했다. runner는 mode `700`, stdout/stderr는 mode `600`,
frozen runtime SHA를 실행 직전에 다시 검증하며 terminal 직전에 자신의 launchd
label을 제거한다. 등록 직후 PID `45555`, run count `1`, state `running`을 확인했다.
wrapper `zsh -n`과 dry-run은 exit `0`, bad input은 exit `2`였다. 기존 forward,
preflight, SIP, finalizer, dataset, research와 Hermes PID는 변경하지 않았다.

CLI 검증은 focused `10 passed`, 전체 `3428 passed`, Ruff 통과,
basedpyright `0 errors, 0 warnings, 0 notes`다. 격리 PEP 723 `--help`, invalid
session과 완전한 임시 control-plane happy path를 실제 subprocess로 실행했고 각각
exit `0/1/0`, stderr `0` bytes, 보고서 mode `600`을 확인했다.

## launchd at-most-once 예약 경계

15:32 KST에 실행된 기존 KR finalizer는 terminal, delivery, 독립 Reviewer와 lifecycle
control 단계가 모두 성공했고 외부 계좌·주문 mutation은 `0`이었다. 그러나 wrapper가
정상 exit `0` 뒤 자신의 launchd label을 제거하지 않아 `launchctl submit` daemon이
약 10초 간격으로 다시 실행했다. 17:29 KST 관측값은 `runs=670`,
`last exit code=0`이었고 lifecycle CSV도 같은 수만큼 증가했다. payload source에는
반복문이 없고 post-session command가 하나뿐이라 application loop가 아니라 scheduler
종료 계약 결손으로 확정했다.

commit `3e565f3a2fc42aabfe1b39f9bdc0fe6e54ded6bc`에 공용
`run_launchd_one_shot.py`를 추가했다.

- timezone-aware 실행시각, 절대 실행파일과 서로 다른 절대 artifact 경로만 허용한다.
- stdout/stderr와 완료 receipt는 mode `600`, wrapper와 atomic claim은 mode `700`이다.
- 실행시각 직전에 claim을 원자적으로 획득해 payload를 at-most-once로 제한한다.
- payload 성공·실패를 private receipt에 기록한 뒤 자기 launchd label을 제거한다.
- 완료 receipt나 기존 claim을 재사용한 새 예약은 `schedule_already_claimed`로 차단한다.
- label, command, receipt 어느 경로도 계좌 식별자나 자격증명을 출력하지 않는다.

실제 임시 launchd label을 현재 코드로 즉시 예약한 수동 QA에서는 `scheduled`,
15초 뒤 label 부재, payload line count `1`, receipt `exit_code=0`을 확인했다.
동일 receipt 재사용은 exit `1`로 차단됐다. CLI `--help`, timezone 없는 bad input과
happy path는 exit `0/2/0`이었고 bad input은 artifact를 만들지 않았다.

focused `2 passed`, 전체 `3430 passed`, 전체 Ruff 통과, basedpyright
`0 errors, 0 warnings, 0 notes`다. 7월 23일과 24일 US 예약 wrapper는 이미 모든
terminal branch에서 자기 label을 제거하므로 실행 중인 PID를 재등록하지 않았다.
사용자 지시대로 기존 KR finalizer와 Hermes service도 중단·변경·재시작하지 않았다.

## US forward 정규장 진행 감사 예약

commit `0103dd519f51c0e742a6870575d260c4dc5b67ab`에 local-only
`run_forward_session_progress.py`를 추가했다. 최종 post-session metrics가 생기기
전에도 regular-session의 ranking coverage, watch cycle, KIS retry/recovery,
candidate input과 Paper recommendation store를 기존 strict quality loader로 함께
감사한다. 필수 artifact 결손, cardinality 불일치, unrecovered·repeated retry,
ranking/watch 실패와 최소 watch cycle 미달은 그대로 차단한다. recovered retry
건수는 관찰 정보로 보존하며 품질 gate를 완화하거나 실패 cycle을 삭제하지 않는다.
성공하더라도 final eligibility는 항상 `pending_post_session`이다.

7월 23일 17:55 KST pre-open 관측에서는 별도 premarket watch cycle `12`,
premarket ranking request `72`, failure `0/0`이었다. regular
`watch_cycles.csv`와 ranking/retry/input artifact가 아직 없는 것은 정규장 시작 전의
의도한 상태였으므로 실제 producer 결함으로 추정하지 않았다.

같은 exact commit의 clean detached runtime을
`/private/tmp/trading-agent-forward-progress-20260723-0103dd5`에 고정하고 다음
at-most-once launchd job을 실제 등록했다.

- `ai.trading-agent.forward-progress-early-20260723`: 2026-07-23 09:45 EDT
  (22:45 KST), minimum watch cycle `8`
- `ai.trading-agent.forward-progress-late-20260723`: 2026-07-23 15:30 EDT
  (2026-07-24 04:30 KST), minimum watch cycle `300`

두 job은 integration worktree의 실제 2026-07-23 session directory만 읽고 각각
`progress/early`, `progress/late`에 mode-600 보고서를 쓴다. 등록 직후 PID는
`84727`, `84825`, run count는 각각 `1`, state는 `running`이었다. wrapper는 mode
`700`, stdout/stderr는 mode `600`, `zsh -n`은 통과했고 실행 전 receipt는 아직
없었다. 기존 KR finalizer, dataset/research watcher와 Hermes service는
중단·변경·재시작하지 않았다.

TDD는 CLI가 없어서 실패하는 RED 뒤 실제 subprocess E2E를 GREEN으로 만들었다.
focused `4 passed`, 전체 `3431 passed`, 전체 Ruff 통과, basedpyright
`0 errors, 0 warnings, 0 notes`다. 격리 PEP 723 `--help`, 완전 fixture,
필수 artifact 결손과 잘못된 minimum 입력은 각각 exit `0/0/1/2`였고 생성된
happy/blocked 보고서는 mode `600`이었다.

## US forward 장전 strict readiness 예약

commit `3d488137ce6d612ebea98dd0b862e1fe9843ef44`에 read-only
`run_forward_premarket_readiness.py`를 추가했다. 장전 원장을 정규장 원장과 섞지
않고 다음 네 artifact를 한 번씩 읽어 combined input SHA-256에 결속한다.

- `premarket_watch_cycles.csv`
- `premarket_ranking_request_coverage.csv`
- `premarket_ranking_snapshots.csv`
- `premarket_risk_screen.csv`

각 watch cycle은 NAS/NYS/AMS의 updown/volume 여섯 request exact set, 성공 status와
양의 row count, snapshot row cardinality, risk-selected candidate identity를 모두
대사한다. cycle 시작과 provider 관측은 180초 이내여야 하고 current New York
session date·premarket window, 최신 cycle age와 최소 최신 후보 수도 함께 검사한다.
실패 cycle·request, 결손·중복·stale artifact는 삭제하거나 완화하지 않고 blocker로
남긴다. 성공도 정규장 또는 post-session 최종 적격성이 아니다.

20:00 KST actual worktree 원장의 수동 CLI 결과는 다음과 같았다.

- result: `ready`
- premarket cycles: `36`
- ranking requests: `216`
- ranking snapshot rows: `13,625`
- latest selected candidates: `10`
- ranking/watch failure: `0`
- report mode: `600`
- provider/account/order mutation: `0`

PEP 723 격리 `--help`가 shared schema import의 `httpx2` 미선언으로 실패하는 결함을
실제 CLI QA에서 발견했다. 같은 isolated subprocess를 RED 테스트로 고정하고 dependency
metadata를 수정한 뒤 GREEN을 확인했다. focused `3 passed`, 전체 `3442 passed`,
Ruff, basedpyright `0 errors, 0 warnings, 0 notes`, no-excuse가 통과했다.

같은 exact SHA의 clean detached runtime
`/private/tmp/trading-agent-premarket-readiness-20260723-3d48813`을 고정하고
`ai.trading-agent.forward-premarket-readiness-20260723` job을 2026-07-23
09:25 EDT / 22:25 KST에 등록했다.

- minimum cycles: `60`
- maximum latest age: `600 seconds`
- minimum latest selected: `1`
- output:
  `outputs/live_sessions/20260723/premarket_readiness/exact-3d488137`
- 등록 직후 state: `running`, run count `1`, PID `64899`
- wrapper mode: `700`
- stdout/stderr mode: `600`
- wrapper `zsh -n`: pass

payload 종료 뒤 atomic receipt를 쓰고 자기 label을 제거하는 공용 at-most-once runner를
사용한다. 이 시점에는 실행시각 전이므로 scheduled state만 검증했으며 실제 `ready`
terminal은 22:25 이후 별도로 확인해야 한다. 기존 forward, preflight, SIP,
option-chain, progress, finalizer, dataset/research job과 Hermes PID는 변경하지 않았다.

## M6 exact option surface 정규장 검증 예약

commit `194818d630ae720e62c7c0bf62fa65d460e73fc5`에 option-contract master와
option-chain terminal을 query-only로 exact identity 결합하는 shadow-only surface를
추가했다. master 없는 snapshot은 artifact 공개 전에 차단하고 master 일부에
snapshot이 없으면 삭제하지 않은 `DEGRADED` coverage로 보존한다. 전 master contract가
결합될 때만 `READY`이며 input terminal SHA-256, canonical contract·underlying
identity, OI, quote/trade, IV와 Greeks를 content-addressed mode-600 JSON에 고정한다.

같은 exact SHA의 clean detached runtime
`/private/tmp/trading-agent-m6-option-surface-194818d`를 고정하고
`ai.trading-agent.m6-option-surface-smoke-20260723` job을 2026-07-23
09:50 EDT / 22:50 KST에 등록했다.

- 기존 22:40 option-chain job의 atomic receipt `exit_code=0`을 선행조건으로 요구한다.
- actual AAPL 2026-07-24 call contract master 77개와 해당 indicative chain DB만
  query-only로 읽는다.
- output:
  `outputs/derivatives/m6_live/2026-07-23/surface-194818d`
- 등록 직후 state: `running`, run count `1`, PID `94779`
- payload와 wrapper mode: `700`
- stdout/stderr mode: `600`
- receipt: 실행 전 pending
- payload `zsh -n`: pass

payload는 실행 직전에 exact runtime SHA와 clean status, New York 정규장, 선행 chain
성공 receipt를 다시 확인한다. surface job 자체에는 credential, provider network,
account 또는 order operation이 없다. 기존 option-chain과 다른 forward, preflight,
SIP, progress, finalizer, dataset/research job 및 Hermes PID는 변경하지 않았다. 이
예약은 아직 actual chain·surface 성공이나 derivatives 성과 증거가 아니다.

## US forward strict post-session closeout 예약

commit `a945ba4116a5e04e6823c2bdb27a02c47015aa9e`에 clean forward session의
post-session chain을 독립 복구·검증하는 local-only closeout을 추가했다. 기존 strict
ranking/watch/retry/candidate progress가 clean이고 post terminal이 전혀 없을 때만
recommendation finalization, metrics, daily research와 adaptive evaluation을 실행한다.
기존 post 실패행, watch 실패와 cycle coverage 결손은 재시도하거나 삭제하지 않는다.

같은 exact SHA의 clean detached runtime
`/private/tmp/trading-agent-forward-closeout-20260723-a945ba4`를 고정하고
`ai.trading-agent.forward-post-session-20260723` job을 2026-07-23
15:55 EDT / 2026-07-24 04:55 KST에 등록했다.

- payload는 16:00 EDT까지 기다린다.
- watch가 post 성공행을 만들면 즉시 query-only exact replay로 검증한다.
- watch가 terminal인데 post 행이 없을 때만 strict closeout을 실행한다.
- watch가 16:15 EDT까지 terminal이 아니면 복구를 가장하지 않고 차단한다.
- output:
  `outputs/live_sessions/20260723/post_session_closeout/exact-a945ba4`
- 등록 직후 state: `running`, run count `1`, PID `7396`
- payload와 wrapper mode: `700`
- stdout/stderr mode: `600`
- receipt: 실행 전 pending
- payload `zsh -n`: pass

등록 뒤 기존 US watch PID `62716`, dataset PID `99876`, research PID `99878`과
Hermes PID `31663`은 그대로였고 어떤 label도 suspend·remove·재시작하지 않았다.
closeout은 provider, credential, account 또는 order endpoint를 사용하지 않는다. 이
예약은 아직 actual clean session, causal dataset, trial 또는 Reviewer 성공 증거가
아니다.
