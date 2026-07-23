# M6 Systematic Independent Reviewer 예약 체크포인트

- 기준일: 2026-07-24 KST
- 구현 커밋: `5c78ee9b45611eb04daf01937e69159d2db3bf7c`
- 상태: 구현·검증·push 완료, actual terminal 검토 예약 대기

## 구현

`run_us_systematic_regime_review.py`는 provider나 broker를 열지 않고 다음 local evidence를 다시 검증한다.

- global experiment ledger의 exact multi-market trial registration과 terminal chain
- persisted systematic card와 target session
- content-addressed completed-daily source
- source에서 다시 계산한 shadow outcome
- terminal event의 card/source/outcome SHA-256 집합

검증이 모두 일치할 때만 별도 mode-600 append-only review ledger에 event를 기록한다. completed는 `continue_collection`, missed-session censor는 `data_quality_review`로 유지한다. 자동 state change, 주문 권한, allocation 변경은 항상 false다. 실행 가능한 Paper champion은 아직 `0/2`이므로 Allocation Manager는 닫혀 있다.

## 실제 preflight

2026-07-23 actual IEX 카드 `us-systematic-regime-20260723-3589f560a1a2a357`에 query-only preflight를 실행했다. 대상 2026-07-24 trial이 아직 terminal 전이어서 `eligible_trials: 0`, review ledger 신규 생성 0건으로 종료했다. open trial을 완료로 바꾸거나 0수익으로 대체하지 않았다.

## 예약

- label: `ai.trading-agent.us-systematic-review-20260724`
- frozen runtime: `/private/tmp/trading-agent-systematic-review-20260724-5c78ee9`
- 실행: 2026-07-24 16:12 EDT, 2026-07-25 05:12 KST
- prerequisite: `us_systematic_finalize.receipt`의 exact `exit_code=0`
- selection: 위 actual card ID 한 건
- semantics: at-most-once receipt/claim, prerequisite 실패 시 nonzero

16:05 EDT finalizer가 exact terminal을 만들지 못하면 Reviewer는 ledger를 생성하지 않고 차단된다. 예약 자체는 성공 증거가 아니며 실행 뒤 receipt, report, review ledger와 terminal artifact를 다시 검증해야 한다.

같은 frozen runtime으로 다음 systematic 정규장의 Reviewer도 연결했다.

- label: `ai.trading-agent.us-systematic-review-20260727`
- 실행: 2026-07-27 16:12 EDT, 2026-07-28 05:12 KST
- prerequisite: 2026-07-27 finalizer receipt의 exact `exit_code=0`
- 기대 terminal cardinality: 누적 exact 2건
- semantics: `--all-terminal` exact replay 뒤 `eligible_trials: 2`가 아니면 nonzero

## 검증

- focused pytest: `38 passed`
- Reviewer/CLI 추가 테스트: `8 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: 통과
- missing exact source: exit `1`, `blocked_source`, review ledger 0건
- fixture happy path: exit `0`, review `1`건, mode `600`
- exact replay: 신규 review `0`, replay `1`
- actual open-trial preflight: exit `0`, eligible/review `0/0`
- broker/account/order/HTTP POST mutation: `0`
