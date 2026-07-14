# Shadow Paper 연구 루프 신뢰성 체크포인트

작성 시각: 2026-07-15 KST

## 범위 결정

현재 우선순위는 broker 주문 엔진 확장이 아니라 급등주 전략의 실시간 forward 검증이다. Alpaca Paper POST/DELETE, 보호주문, 주문 stream 단일 writer 확장은 동결하고 다음 최소 루프만 활성 범위로 유지한다.

1. KIS 읽기 전용 상승률·거래량 후보 수집
2. ORB 등 독립 전략의 시점 인과적 조건부 추천
3. 다음 완료 분봉부터 보수적 shadow 체결·손절·목표·장마감 종료
4. 비용별 PF·승률·평균수익·누적수익·MDD·bootstrap CI 일일 평가

## 발견한 운영 결함

2026-07-14 실시간 watch는 AMEX 상승률 랭킹 HTTP 500이 발생할 때 NASDAQ·NYSE 응답까지 폐기하고 child scan을 traceback으로 종료했다. 프로세스는 계속 살아 있었지만 해당 cycle의 후보·분봉 평가가 전부 빠졌다. 메모리 문제는 아니었다. watch Python·RSS guard 전체는 수십 MiB 수준이었다.

## 수정한 계약

- 거래소×랭킹 종류 요청을 독립 처리한다.
- 정상 응답은 `RankingGroup`, 공급자 실패는 `RankingFailure`로 분리한다.
- 성공 그룹은 위험 게이트와 shadow 전략 평가를 계속 통과한다.
- 모든 요청은 `kis_ranking_request_coverage.csv`에 `ok/failed`, 행 수, 안전하게 축약한 실패 사유를 남긴다.
- 실패가 하나라도 있으면 한국어 보고서에 `부분 모집단`을 표시하고 cycle 종료코드는 1이다.
- KIS의 상위 랭킹 표본을 전체 미국시장 PIT 모집단으로 표현하지 않는다.

## 장마감 평가 연결

기존 `run_paper_metrics.py`는 수동 실행만 가능했다. watch가 공식 정규장 종료 뒤 끝날 때 세션 SQLite를 대상으로 자동 실행하고 다음을 남기도록 연결했다.

- `paper_metrics/paper_metrics.csv`
- `paper_metrics/paper_yearly_metrics.csv`
- `paper_metrics/paper_trades.csv`
- `paper_metrics/paper_metrics_ko.md`
- `post_session_metrics_cycles.csv`

장중 단발 watch와 DB가 없는 실행은 일일 평가를 만들지 않는다. 종료되지 않은 추천은 거래 성과에서 제외한다.

## 검증 증거

- AMEX 상승률 요청만 HTTP 500인 모의 wire 응답에서 총 6개 요청을 끝까지 수행하고 성공 그룹 5개와 실패 1개를 분리했다.
- 관련 테스트 22개와 이후 전체 테스트 427개가 통과했다.
- Ruff 변경 파일 검사와 basedpyright 전체 검사는 통과했다.
- 실제 KIS 읽기 전용 QA는 후보 3개, 분봉 archive 320개, 최대 RSS 66,994,176 bytes를 기록했다. 랭킹 6개는 모두 성공했고 종목별 SHPH 일봉 HTTP 500 때문에 전체 CLI는 종료코드 1을 유지했다. 다른 두 종목의 분봉은 보존됐다.
- 같은 QA DB의 장마감 metrics 경로는 종료코드 0으로 5/10/20bp 3개 행을 만들었다. 완료 거래가 0건이므로 승률·PF·평균·누적·MDD·CI는 모두 `N/A`로 유지됐다.
- CLI `--help`는 0, 잘못된 `--top 0`과 존재하지 않는 metrics 입력은 2를 반환했다.

실제 QA 산출물:

`outputs/trading_strategy_research_hub/02_momentum_strategies/03_recommendation_agent/live_runs/20260715_partial_ranking_recovery_qa_001/`

## 해석 제한

이 변경은 수익성을 증명하지 않는다. 2026-07-14 ORB forward 추천 2건도 기능·인과성 표본이며 전략 채택 근거가 아니다. 최소 60거래일·100건, 동일 위험의 champion/challenger 비교와 사전 승격 기준을 충족하기 전에는 확정 수익 전략으로 표현하지 않는다.
