# Multi-market experiment ledger v4 체크포인트

## 완료 범위

- global experiment ledger를 schema v4로 올리고 exact `StrategyLaneRef`를 보존하는 `multi_market_hypotheses`, `multi_market_strategy_versions` append-only table을 추가했다.
- v1, v2, v3 원장은 기존 row/payload를 재작성하지 않고 다음 Writer lease에서 v4 객체만 원자적으로 추가한다.
- multi-market scope는 한 시장 안의 sorted unique lane만 허용하고, cross-lane 결합은 별도 hypothesis ID·두 개 이상의 source hypothesis·명시적 combination rule을 요구한다.
- operating mode는 `contract_only`, `shadow`를 모든 유효 lane에 허용하되 `alpaca_paper`는 US day/swing에만 허용한다. KR lane은 legacy `LaneId`로 위장하지 않는다.
- 기존 legacy table과 multi-market table 사이의 hypothesis/version identity 충돌, 부모 scope/lane/time 불일치, payload·normalized column·content key 변조를 fail-closed한다.

## KR vertical 연결

- `run_kr_theme_research_register.py`가 `kr_equities/opportunity_manager/theme_momentum`의 가설과 code-coupled strategy version을 `shadow`로 사전등록한다.
- `run_kr_theme_projection.py`는 `--experiment-ledger`를 필수로 받고 manifest의 producer strategy version, runtime code version과 projected-at causality를 exact 등록 행에 대사한 뒤에만 KR classification/Opportunity을 만든다.
- source ledger와 experiment ledger 및 각 SQLite sidecar가 projection artifact와 path, symlink 또는 hard-link로 겹치면 원장을 열기 전에 차단한다.
- 등록, projection과 outbox는 exact replay에서 추가 행을 만들지 않으며 SQLite/report/outbox는 mode `600`을 유지한다.

## 경계

- 이 체크포인트의 KR agent는 ranked `OpportunitySnapshot`까지만 만든다. KR day `TradeSignal`, current quote adapter, shadow fill, trial terminal, Reviewer/lifecycle은 아직 연결하지 않았다.
- multi-market `ResearchSource` card와 multi-market trial/lifecycle schema도 아직 없다. 기존 US legacy trial/lifecycle을 KR에 억지로 재사용하지 않는다.
- provider, credential, network, 계좌·포지션·주문 endpoint와 broker mutation은 0건이다.
- synthetic fixture 결과는 분류 정확도, 실전 수익성 또는 승격 근거가 아니다.

## 검증

- focused schema/store/KR E2E: `124 passed`
- 전체 회귀: `2630 passed`
- Ruff: 통과
- changed-file Ruff format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall: 통과
- 새 multi-market/KR registration 모듈 no-excuse: 위반 0건
- 수동 registration CLI: help `0`, 오입력 `1`, first/replay `0/0`
- 수동 projection CLI: help `0`, 미등록 원장 차단 `2`, first/replay `0/0`
- 수동 replay 결과: hypothesis/version/classification/Opportunity `1/1/1/1`, experiment DB/outbox/report mode `600`, external mutation `0`

## 다음 단계

KR theme Opportunity의 exact producer lineage를 보존한 채 현재 provider-neutral market gate를 통과한 후보만 별도 KR day shadow `TradeSignal`로 변환한다. LS/KIS quote adapter, 비용 모델, shadow fill과 terminal outcome은 각각 독립 계약으로 추가하며 국내 주문 권한은 계속 닫는다.
