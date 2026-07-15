# 다중 시장 Agent 공통 계약 체크포인트

- 날짜: 2026-07-15
- 브랜치: `codex/multi-market-agent-contracts`
- 기준: `e2a8e0f`
- 범위: 통합 설계 Milestone 1

## 구현 결과

기존 실행 중심 `LaneId`와 DB schema를 변경하지 않고 그 위에 연구·추천 계약을 추가했다.

- `MarketId`: `us_equities`, `kr_equities`
- `AgentFamily`: Opportunity Manager, Day, Swing, Systematic Quant, Market Context, Allocation Manager
- `StrategyLaneRef`: `market/agent/strategy` canonical 연구 좌표
- `AgentManifest`: 시장·agent·출력 종류·운영 mode·전략 lane 불변 계약
- `LegacyExecutionLaneBinding`: US day→intraday, US swing→swing, US context→market regime만 허용
- `StrategyVersionRef`와 `CompositeExperimentSpec`: 서로 다른 lane 버전의 동일 시장·사전등록 조합
- `OpportunitySnapshot`: 후보 순위, producer version, source coverage, evidence와 관찰시각 계약
- `TradeSignalEnvelope`: 진입·손절·목표·만료·근거와 조건부/현재호가검증 상태 계약
- `project_intraday_recommendation()`: 기존 US day `RecommendationState.SETUP`을 conditional signal로 투영

새 공통 계약 모듈은 credential, HTTP, broker client, execution mutation 또는 DB 모듈을 import하지 않는다.

## 인과성과 시장 격리

- evidence와 source observation은 Opportunity 또는 Signal의 `observed_at`보다 늦을 수 없다.
- Opportunity source coverage 중 하나라도 불완전하면 snapshot을 생성하지 않는다.
- US symbol과 KR 6자리 symbol 규칙을 시장 좌표에 따라 검증한다.
- KR strategy는 어떤 기존 Alpaca Paper execution lane에도 binding할 수 없다.
- 에이전트 조합은 component version, 결합 규칙, 등록시각과 효력시각을 고정한 새 experiment여야 한다.
- `CURRENT_QUOTE_VALIDATED` 신호는 quote 시각·유효기간·bid/ask·spread·허용 slippage가 모두 맞아야 한다.
- 기존 recommendation projection은 완료·활성·무효·인과성 제외 상태를 새 진입 신호로 되살리지 않고 `SETUP`만 허용한다.

## TDD 증거

각 production 모듈을 만들기 전에 대응 테스트를 실행해 새 모듈 또는 새 class 부재로 RED를 확인했다.

| 계약 | RED 원인 | GREEN |
|---|---|---:|
| 연구 identity·legacy binding | `research_identity_models` 없음 | 11 tests |
| composite experiment | `composite_experiment_models` 없음 | 5 tests |
| Opportunity | `signal_contract_models` 없음 | 5 tests |
| Trade Signal | signal class import 불가 | 11 tests 합계 |
| Recommendation projection | `recommendation_signal_projection` 없음 | 11 tests |

관련 새 계약 테스트를 함께 실행한 결과는 `38 passed`다. 기존 alert와 trading engine을 포함한 projection 회귀는 `27 passed`다.

## 자동 검증

- 기준선: `946 passed`
- 구현 후 전체 pytest: `984 passed`
- 전체 Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `git diff --check`: 통과

## 수동 CLI 회귀

- `run_trading_agent_replay.py --help`: exit 0, 필수 input과 output/range option 확인
- 존재하지 않는 CSV: exit 2, 입력 파일 오류로 차단
- `examples/example_intraday.csv` happy path: exit 0, 분봉 7개·추천 1개·신규 카드 1개
- happy path 산출물: recommendation JSONL, 한국어 카드, 한국어 보고서, SQLite 원장 확인

## 커밋

- `9266b79 feat: add multi-market agent identities`
- `a0c3edf feat: add composite strategy experiment contract`
- `a29b529 feat: add opportunity and trade signal contracts`
- `b16aa0d feat: project intraday recommendations to signals`

## 변경하지 않은 것

- 기존 `LaneId`, lane registry, experiment ledger와 execution schema
- ORB/VWAP/HOD/Gap-and-Go 계산 및 outbox schema
- KIS·Alpaca provider 호출
- Alpaca Paper arm, account binding, 위험 한도와 mutation 경로

## 다음 마일스톤

1. 현재 KIS US ranking 후보를 `OpportunitySnapshot`으로 투영한다.
2. 기존 네 intraday 전략의 signal을 공통 envelope와 local outbox에 연결한다.
3. quote freshness를 통과한 신호와 조건부 신호를 사용자 카드에서 구분한다.
4. 그 다음 KR Theme T0 raw catalyst·coverage 원장을 구현한다.

KR 촉매 수집, KR LLM 분류, 실제 swing signal engine, systematic quant 논문 replication, lifecycle v2 `SHADOW_CHAMPION`, Allocation Manager와 외부 알림 adapter는 이번 체크포인트에 포함하지 않았다.
