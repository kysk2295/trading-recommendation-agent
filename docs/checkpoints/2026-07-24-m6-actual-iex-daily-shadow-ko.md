# M6 actual IEX completed-daily shadow 체크포인트

## 범위와 안전 경계

2026-07-23 NYSE 장후에 Alpaca Market Data의 완료 일봉만 읽어 US swing과
systematic shadow vertical을 실제 source-backed 경로로 실행했다.

- market-data `GET`만 사용했다.
- feed는 `iex`를 명시했으며 SIP 실패 뒤 자동 fallback하지 않았다.
- Paper account·position·order API, HTTP `POST`/`DELETE`, 외부 메시지와
  Allocation Manager를 호출하지 않았다.
- IEX-only evidence는 Challenger까지만 사용할 수 있고 SIP 또는 동등한
  consolidated feed 검증 전에는 Paper Champion 근거가 아니다.

## 선행 SIP 차단과 명시적 feed 계약

기존 swing production 경로는 일봉 `feed=sip`를 고정해 현재 credential에서 HTTP
`403`으로 닫혔다. 이는 이미 보존된 SIP entitlement 차단 증거와 일치했다.

`21813099f68c46c2ad2043d3a76e9b0fa4549e34`는 다음 계약을 추가했다.

- production `--feed iex|sip` 필수
- fixture와 feed 혼합 차단
- `DataSourceId(provider, feed)`를 포함한 completed-daily source schema v2
- exact source key 이름의 mode-600 immutable JSON
- 신호·시장 context evidence ref와 source key 결속

관련 전체 회귀는 `3565 passed`, Ruff 전체 통과, basedpyright
`0 errors, 0 warnings, 0 notes`였다.

## actual swing IEX 결과

clean `21813099f68c46c2ad2043d3a76e9b0fa4549e34`에서 정렬된 50종목 bounded
universe를 실행했다.

- session: `2026-07-23`
- source identity: `alpaca/iex`
- symbol / daily bar: `50 / 1600`
- source key:
  `621f37629762eb355d5886ee5a3ea74c2634bdc21772a21307dc825520892631`
- source file SHA-256:
  `f189f207b8749efdf5bee8f4d014a75f355ceed382a12e3a0aa7c5f3817c438b`
- source mode: `600`
- signal / trial: `0 / 0`
- terminal delivery: `no_recommendation`

고정 20-session new-high·RVOL 1.5 조건을 만족한 종목이 없어 trial을 만들지 않았다.
이 결과는 clean source 성공이지 성과 또는 추천 성공이 아니다. completed delivery
뒤 존재하지 않는 credential 경로로 재실행했을 때 scanner는 실행되지 않았고 source
SHA와 signal/trial 수가 유지됐다.

## actual systematic IEX 카드와 다음 세션 trial

`5fbff5f80e3562960cdbc1e1593343d63a4ecc69`에서 고정 ETF 6종의 actual IEX
completed-daily source를 수집했다.

- observed at: `2026-07-23T16:48:31.830228-04:00`
- source identity: `alpaca/iex`
- symbol / daily bar: `6 / 1776`
- source key:
  `bca738d3c1eccf92286bfaaa6b42ebb9e79c8da4d9643b4418b5a858ac4afc86`
- source file SHA-256:
  `cb53d3119c7073376e7475ba05d5a6805f0bbf55aed23b481796b6ba61a765eb`
- source mode: `600`
- card:
  `us-systematic-regime-20260723-3589f560a1a2a357`
- target session: `2026-07-24`
- trial:
  `us-systematic-regime-20260724-6fdb16d5a8ea7154`
- card / trial: `1 / 1`
- regime / breadth: `mixed / 1 of 3`
- decision: `no_recommendation`
- candidate / order mutation: `0 / 0`

mixed regime은 0수익 추천으로 바꾸지 않고 명시적 no-recommendation으로 유지했다.
trial은 다음 세션 lifecycle을 관측하기 위한 `shadow_forward` 등록이며 entry 또는
주문 권한이 아니다.

## exact replay와 중복 방지

production 장후 재실행이 새 관측시각으로 source를 다시 조회하고 같은 세션의 다른
code version 카드·trial을 만들 수 있던 결손을 두 단계로 닫았다.

- `234c27ef450ceecd45ceabef252c7953888aeae7`: private source artifact를
  owner/mode/single-link/content-addressed 조건으로 먼저 읽고 exact
  session·`alpaca/iex` source를 credential 전에 재사용
- `3bfcf8ebc6e5fcd94c17fcb6a6c3e61524aef9e8`: 이미 published card와 exact
  trial이 결속된 source는 runtime code SHA가 바뀌어도 신규 card/version/trial을
  만들지 않고 completed replay로 종료

clean `3bfcf8ebc6e5fcd94c17fcb6a6c3e61524aef9e8`에서 존재하지 않는 credential
경로로 actual replay를 실행한 결과는 다음과 같았다.

- exit: `0`
- source SHA unchanged: `true`
- cards created / trials registered: `0 / 0`
- persisted source / card / trial: `1 / 1 / 1`
- account access / order mutation / HTTP POST: `0 / 0 / 0`

관련 회귀는 `47 passed`, Ruff 통과, basedpyright `0/0/0`이었다.

## 다음 운영 단계

2026-07-24 정규장 start와 장후 terminal을 다음 frozen runtime의 at-most-once
launchd job으로 등록했다.

- frozen runtime:
  `/private/tmp/trading-agent-systematic-20260724-e510dab`
- exact commit:
  `e510dab66cc3d515fb753ed03895cea4b6f9e647`
- start label / 시각:
  `ai.trading-agent.us-systematic-start-20260724`,
  `2026-07-24 09:31 EDT` (`22:31 KST`)
- finalize label / 시각:
  `ai.trading-agent.us-systematic-finalize-20260724`,
  `2026-07-24 16:05 EDT` (`2026-07-25 05:05 KST`)
- wrapper / log mode: `700 / 600`
- atomic claim·완료 receipt: enabled
- 시작 전 상태: label running/sleeping, runs `1`, receipt·claim 없음, log `0 bytes`

start tick은 provider·credential 없이 exact registered trial만 시작한다. finalize tick은
명시적 IEX completed-daily GET 뒤 prior trial terminal과 다음 카드 등록을 함께
처리한다. start가 누락되면 finalize는 no-position 성과를 추정하지 않고 실패한다.
두 job은 기존 US forward·finalizer, KR finalizer와 Hermes를 변경하지 않았다.

- 2026-07-24 정규장에는 예약된 systematic trial start 결과를 검증하고 장후 actual
  source로 no-position terminal을 확정한다.
- swing은 다음 실제 장후 source에서 고정 신호가 생길 때만 prospective trial을
  등록한다.
- SIP entitlement가 복구되기 전에는 IEX evidence를 Paper Champion으로 승격하지
  않는다.
- executable Paper champion 2개 전에는 Allocation Manager를 활성화하지 않는다.
