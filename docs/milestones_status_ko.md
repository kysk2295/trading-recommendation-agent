# 제품 우선 마일스톤 현황

권위: `docs/superpowers/specs/2026-07-17-institutional-multi-market-quant-research-os-design.md` §17

갱신: 2026-07-22

이 문서는 connector, DB schema 또는 fixture 개수를 진척도로 세지 않는다. 사용자가 실제로 받는
추천과 Paper/shadow 결과를 기준으로 상태를 표시한다.

## 현재 상태

| M | 사용자 결과 | 상태 | 아직 필요한 실제 증거 |
|---|---|---|---|
| 0 | 제품 우선순위 전환 | **완료** | 없음 |
| 1 | US·KR 추천의 Telegram/Hermes 전달 | **진행 중** | 외부 전달 adapter, 실제 세션 카드 또는 무추천 결과 |
| 2 | US Day Agent Alpaca Paper 수직 | **코드 준비 / 운영 미완료** | 실제 Paper POST 1건 이상, 보호주문·exit/EOD·대사·전달 완주 |
| 3 | KR Theme Day Agent shadow 수직 | **fixture 준비 / live 미완료** | 열린 KRX same-cycle, 실제 카드와 shadow entry/exit·전달 |
| 4 | 상시 운전과 일일 요약 | **부분 구현** | 재부팅 포함 US·KR 각 5거래일 자동 결과 |
| 5 | US Swing Agent | **fixture E2E** | 실제 post-close 후보와 다중세션 shadow lifecycle |
| 6 | Loop Engineer 실제 연구 루프 | **계약 일부 / 제품 루프 미완료** | challenger 1개의 source→Reviewer 자동 완주 |
| 7 | Systematic·Derivatives Agent 확장 | **조기** | M6 이후 lane별 사용자 수직 |
| 8 | Allocation Manager | **금지** | 두 독립 executable champion |

현재 활성 구현 마일스톤은 **M1 추천 전달**이다. M2와 M3의 실제 장이 열리면 새 기능을 넓히지 않고
이미 준비된 경로의 운영 증거만 수집한다.

## 바로 실행할 작업

1. 기존 US/KR 카드와 outbox를 Hermes의 Telegram 전달 계약에 연결한다.
2. 카드가 없을 때도 무추천·stale·source 불완전·휴장 이유를 한 번 전달한다.
3. US 정규장에서는 ORB baseline 하나의 scan→card→armed Paper→OCO→EOD→daily result만 완주한다.
4. KRX 정규장에서는 theme leader 하나의 same-cycle→card→shadow entry/exit→daily result만 완주한다.
5. 두 시장의 실제 세션이 닫힌 뒤에야 재부팅 가능한 상시 스케줄과 통합 일일 요약을 M4로 닫는다.

## 마일스톤 완료 규칙

각 마일스톤은 아래 증거가 모두 있어야 완료다.

- 사용자가 Telegram/Hermes에서 결과를 확인할 수 있다.
- 실제 현재 시장 read-only data 또는 Alpaca Paper/shadow lifecycle이 사용됐다.
- 추천이 없거나 차단된 경우도 사유가 전달됐다.
- 재실행과 process restart가 중복 카드·중복 주문을 만들지 않았다.
- fixture happy path, invalid/stale input, 실제 CLI 또는 서비스 surface를 수동 QA했다.
- 변경 범위 pytest, 전체 pytest, Ruff와 basedpyright가 통과했다.

fixture, `--help`, schema migration, receipt 저장 또는 mock broker만 통과한 상태는 **코드 준비**이며
제품 완료가 아니다. 실제 장 조건이 없으면 주문이나 추천을 강제로 만들지 않고 상태를 미완료로
정확히 남긴다.

## 인프라 동결

다음 작업은 현재 활성 수직이 실제로 실패한 원인이 확인되기 전까지 시작하지 않는다.

- 새 ledger, schema, receipt, supervisor, attestation 또는 범용 control-plane 계층
- 활성 challenger가 요구하지 않은 options, futures, social 또는 신규 provider adapter
- 새 lane을 위한 선제적 framework와 big-bang 디렉터리·서비스 재구성
- fixture-only 체크포인트를 늘리는 vertical 복제
- 실사용 장애 증거가 없는 recovery·security micro-milestone
- RD-Agent, TradingAgents, Qlib, NautilusTrader, LEAN, OpenBB의 제품 코어 통합

지원 코드가 필요하면 현재 실패한 사용자 시나리오, 최소 변경, 같은 수직의 실제 재검증을 한 작업
묶음으로 남긴다. standalone infrastructure commit은 만들지 않는다.

## 제품 완료 구간

- **운영 제품 v1:** M1~M4 완료
- **Research OS v1:** M1~M6 완료
- **전문 다중 Agent 목표:** M1~M7 완료
- **Allocation:** M8 gate가 자연스럽게 열린 뒤 별도 완료

`Opportunity Manager`가 종목을 발굴한다. `Allocation Manager`는 두 champion 이후 다음 세션의
위험예산만 계산하며 종목 발굴이나 주문을 하지 않는다.

## 영구 금지

- 실자금 거래와 Alpaca live endpoint
- KIS·LS의 계좌·주문 mutation
- 무단 소셜 크롤링과 데이터 재배포
- LLM의 재량 주문, 위험한도 변경 또는 독립 승격
- 성과 근거 없는 위험한도 확대
- backtest·fixture·Paper 결과를 확정수익으로 표현
