# KR source readiness·chain rollover·다음 세션 예약 체크포인트

## 결과

2026-07-24 현재 실행 중인 KR shadow 세션의 품질 게이트는 유지했다. 실제 secret loader를
network·계좌·주문 호출 없이 점검한 결과 LS NWS와 KIS live 설정은 사용할 수 있었지만
OpenDART 설정은 없었다. 따라서 당일 09:05 source cycle은 실패 증거를 보존했고 15:32
finalizer가 이를 `CENSORED` data-quality terminal로 닫도록 그대로 두었다.

다음 공식 KRX open session인 2026-07-27에는 같은 결손을 장중에 처음 발견하지 않도록
08:30 KST source readiness와 08:55~15:32 실제 KR M3 chain을 각각 독립 at-most-once
작업으로 예약했다. 품질 게이트를 완화하거나 실패 cycle을 지우지 않는다.

## 구현

`run_kr_same_cycle_source_readiness.py`는 다음 설정을 source별
`ready`/`unavailable`로만 보고한다.

- OpenDART read-only list source
- LS NWS read-only source
- KIS live read-only source

이 CLI는 provider network, account, balance, position, order endpoint를 열지 않고
자격증명 값과 파일 경로를 보고서에 쓰지 않는다. 모든 source가 준비됐을 때만 exit 0,
하나라도 사용할 수 없으면 aggregate report를 mode 600으로 남기고 exit 1이다.

운영 ledger의 이전 immutable rollover bundle은 원래 registration manifest가 없어도
다음 code-coupled research version으로 승계할 수 있게 했다.
`run_kr_theme_research_chain_rollover.py`는 이전 bundle의 canonical bytes, exact 두
strategy version, hypothesis cardinality, shadow mode, policy 결합과 ledger registration을
먼저 대사한다. 그 뒤 새 commit SHA의 Opportunity/day version 두 건과 policy를 만들고
content-addressed bundle을 mode 600으로 발행한다. 같은 SHA 재실행은 기존
`ledger_recorded_at`과 exact row를 재사용하며 version을 늘리지 않는다.

## 실제 운영 결속

- 실행 code SHA:
  `e910816ef9dcc7a3404331b33d72251aa14702e4`
- frozen runtime:
  `/private/tmp/trading-agent-kr-m3-20260727-e910816`
- rollover bundle SHA-256:
  `732b705cc35730f7e16319f94a65bc8daa919d8bad402275dc3bbacd3e47eac5`
- Opportunity version:
  `kr-theme-keyword-projection-v1-code-079535f8f403927a`
- Day version:
  `kr-theme-leader-vwap-reclaim-v1-code-079535f8f403927a`
- 최초 rollover / exact replay exit:
  `0 / 0`
- 최초 / replay 신규 version:
  `2 / 0`

frozen runtime은 detached clean Git worktree이고 `require_runtime`이 exact HEAD와
porcelain-empty 상태를 실행 전에 다시 확인한다.

## 예약

| KST 시각 | label | 동작 |
|---|---|---|
| 2026-07-27 08:30 | `ai.trading-agent.kr-source-readiness-20260727` | source별 secret-file contract 사전점검 |
| 2026-07-27 08:55 | `ai.trading-agent.kr-m3-20260727` | official calendar, composite와 trial 등록 |
| 2026-07-27 09:00 | 같은 KR M3 chain | shadow trial start |
| 2026-07-27 09:05 | 같은 KR M3 chain | exact four-source cycle과 Opportunity projection |
| 2026-07-27 15:32 | 같은 KR M3 chain | terminal, 독립 Reviewer와 lifecycle evidence |

두 label은 설치 직후 각각 `runs=1`, `state=running`, `last exit code=(never exited)`로
대기함을 확인했다. runner/wrapper는 mode 700, stdout/stderr log와 policy/bundle은
mode 600이다. KR chain은 receipt와 claim으로 한 번만 실행된다.

08:30 readiness 실패는 09:05 품질 게이트를 우회하지 않는다. OpenDART 설정이 그때도
없거나 다른 source가 준비되지 않으면 실제 source cycle은 실패를 보존하고 15:32에
성과 표본이 아닌 censored terminal로 닫힌다.

## 검증

- chain rollover red test: CLI 부재로 exit 2
- chain rollover focused: `3 passed`
- repository full suite: `3610 passed`
- Ruff whole repository: pass
- basedpyright whole repository: `0 errors, 0 warnings`
- chain CLI manual QA: help `0`, invalid SHA `1`, actual-ledger happy/replay `0/0`
- readiness CLI manual QA: help `0`, invalid input `2`, synthetic ready `0`,
  actual missing-source `1`
- KR runner: `zsh -n` pass, 이전 운영 runner에 날짜·epoch·SHA·version만 바꾼
  exact template comparison pass

실제 자금, 국내 계좌, 주문 mutation과 Allocation Manager activation은 모두 0건이다.

## 다음 판정

2026-07-27 15:32 이후 session verifier와 open-smoke evidence가 clean terminal을
확인한 경우에만 causal research dataset과 READY data foundation의 후보가 된다.
결손·censored·failed session은 catalog에 보존하되 CSV, walk-forward 성과 또는
Paper champion 표본으로 승격하지 않는다.
