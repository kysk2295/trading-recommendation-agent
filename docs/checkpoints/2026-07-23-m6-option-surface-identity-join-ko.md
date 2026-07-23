# M6 exact option surface identity join 체크포인트

## 제품 결과

서로 분리된 option-contract security master와 option-chain snapshot을 contract identity
추정 없이 결합하는 bounded shadow-only surface를 추가했다.

```text
successful contract-master terminal + successful chain terminal
→ exact request scope and as-of ordering
→ OCC provider alias to canonical contract UUID
→ underlying UUID / expiration / right / strike reconciliation
→ OI + quote/trade + IV + Greeks
→ content-addressed private surface
```

구현 SHA는
`194818d630ae720e62c7c0bf62fa65d460e73fc5`다. 두 SQLite store는 query-only로
열며 credential, provider network, account, position, order API를 사용하지 않는다.

## 결합·실패 계약

- master와 chain은 underlying, exact expiration, call/put scope가 같아야 한다.
- master terminal은 chain terminal보다 늦을 수 없다.
- provider alias는 canonical contract instrument ID와 exact하게 결속되고 surface
  관측시각에도 유효해야 한다.
- chain의 모든 OCC symbol은 master에 정확히 하나 존재해야 한다. master가 없는
  snapshot은 artifact와 aggregate report 공개 전에 fail-closed다.
- matched snapshot은 underlying, expiration, right, strike가 master와 다시 일치해야
  한다.
- master contract는 snapshot이 없더라도 삭제하지 않는다. 이 경우
  `snapshot_present=false`와 coverage 결손을 보존한다.
- master 전부에 snapshot이 있을 때만 `READY`, 일부 또는 전부가 없으면
  `DEGRADED`이고 CLI는 exit `2`다.
- output은 master와 chain terminal의 canonical SHA-256, exact request/run ID,
  canonical contract와 underlying identity, multiplier, tradable, OI·as-of,
  close·as-of, quote/trade, IV, Greeks를 포함한다.
- artifact 이름은 canonical surface content SHA-256이며 exact replay는 같은 파일을
  재사용한다.
- artifact와 aggregate report는 mode `600`, private 부모는 mode `700`이다.
- 이 경로는 recommendation, lifecycle, allocation 또는 order authority를 바꾸지
  않는다.

## TDD와 수동 QA

CLI가 없는 상태의 import failure를 RED로 확인한 뒤 세 public boundary를 구현했다.

- exact master와 chain: `READY`, coverage `10,000 bps`
- snapshot identity가 master에 없음: artifact 없이 `BadParameter`
- request scope 불일치: output directory 생성 전에 `BadParameter`
- 집중 테스트: `3 passed`
- 전체 pytest: `3449 passed in 211.39s`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`

실제 CLI surface에서 `--help`, invalid date, happy path와 exact replay를 실행했다.
invalid date는 exit `2`와 output 미생성, happy/replay는 exit `0/0`이었다. fixture
surface는 `READY`, exact join `1`, coverage `10,000 bps`, 최초
`artifact created: yes`, replay `no`였고 artifact와 report mode는 모두 `600`이었다.

## 실제 정규장 검증 예약

기존 22:40 KST option-chain smoke는 변경하지 않았다. 구현 SHA의 별도 clean detached
runtime을 `/private/tmp/trading-agent-m6-option-surface-194818d`에 고정하고, chain
종료 뒤 actual contract master와 chain DB를 결합하는 신규 at-most-once 작업을
예약했다.

- label: `ai.trading-agent.m6-option-surface-smoke-20260723`
- 실행시각: 2026-07-23 09:50 EDT / 22:50 KST
- 선행 gate: 기존 `m6_option_chain_smoke.receipt`가 존재하고 `exit_code=0`
- contract input: actual AAPL 2026-07-24 call master 77개, limit 10,000
- chain input: AAPL 2026-07-24 call `indicative`, limit 1,000
- output:
  `outputs/derivatives/m6_live/2026-07-23/surface-194818d`
- 등록 직후 상태: running, run count `1`, PID `94779`
- payload와 wrapper mode: `700`; stdout/stderr mode: `600`
- 등록 직후 receipt: pending

payload는 exact runtime SHA와 clean status, New York 날짜와 09:30~16:00 정규장,
선행 chain 성공 receipt를 모두 다시 확인한다. surface 작업 자체에는 credential과
network 호출이 없다. 선행 chain이 실패·지연되거나 exact DB terminal이 없으면
surface를 만들지 않고 nonzero receipt로 닫힌다.

이 예약은 actual surface 성공 증거가 아니다. 실행 뒤 chain receipt·terminal과
surface status, identity coverage, input SHA, mode를 다시 검증해야 한다. 단일
AAPL·단일 만기·call의 bounded slice는 시장 전체 options coverage, OPRA entitlement,
IV surface·skew·term structure, 파생상품 전략 성과, 추천 또는 주문 권한을 뜻하지
않는다.
