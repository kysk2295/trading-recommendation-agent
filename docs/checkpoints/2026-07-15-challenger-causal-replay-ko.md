# Challenger 장마감 causal replay 체크포인트

작성일: 2026-07-15 KST

## 목적

ORB champion 수집 세션에서 당시 실제로 저장된 후보 입력과 최초 관찰 분봉만 사용해 VWAP reclaim, HOD breakout, Gap-and-Go를 독립 shadow 재생한다. 현재 일봉 문맥이나 사후 선정 종목을 끼워 넣지 않고, 데이터가 불완전한 종목과 날짜를 수익 0으로 바꾸지 않는다.

## 입력 게이트

- 일일 품질의 랭킹 6요청×cycle, watch 종료코드, KIS read retry 감사, 후보 입력 cycle과 SQLite 행 수가 모두 일치해야 한다.
- `post_session_metrics_cycles.csv`에 같은 뉴욕 거래일 정규장 종료 뒤 성공행이 있어야 한다.
- 모든 후보 snapshot은 실제 관찰시각보다 최소 1분 전에 시작한 완료 봉까지만 가리켜야 한다.
- 전일 종가·20일 평균 거래량·spread와 분봉 OHLCV는 유한하고 가격 관계가 유효해야 한다.
- 한 ticker가 여러 거래소 코드로 들어오면 symbol 기반 checkpoint 충돌을 피하기 위해 날짜 전체를 거부한다.
- 해당 거래일 정규장 1분 시계열이 정확히 완성된 종목만 결과 추적에 사용한다. 불완전 종목은 `censored`이며 무거래 또는 0수익이 아니다.

## 재생 계약

각 후보 관찰시각마다 다음 두 조건을 모두 만족하는 분봉만 scanner와 strategy에 넣는다.

1. 분봉의 최초 관찰시각이 후보 snapshot 관찰시각 이하
2. 분봉 시작시각이 snapshot의 최신 완료 봉 이하

라이브 KIS child가 매 cycle 새 프로세스로 시작하는 동작을 재현하기 위해 snapshot마다 새 scanner·strategy 인스턴스를 만들고 SQLite checkpoint를 다시 읽는다. 마지막 snapshot 뒤에는 이미 생성된 추천의 체결·손절·목표·장마감 상태만 완전한 당일 경로로 갱신한다. 그 경로에서 새 신호는 만들지 않는다.

## 독립 산출물

전략별 출력 폴더에는 다음을 분리 저장한다.

- `challenger_replay_gate.json`, `challenger_replay_gate_ko.md`
- `symbol_coverage.csv`
- `paper_recommendations.sqlite3`, `recommendations_ko.md`
- `paper_metrics/paper_metrics.csv`, 연도별·거래별 CSV와 한국어 보고서

ORB는 challenger CLI에서 거부한다. 성공한 raw replay도 ORB와 동일 최대 포지션·위험 예산으로 재선정한 결과가 아니므로 `comparison_eligible=false`와 `portfolio_comparison_not_implemented`를 고정한다. 자동 주문·자동 승격 근거가 아니다.

## 검증 결과

- 합성 2026-07-14 정규장 390봉과 시점 고정 Gap-and-Go 입력을 CLI로 재생해 추천 1건, 완료 거래 1건, 편도 5/10/20bp metrics를 생성했다.
- 같은 source의 5봉뿐인 두 번째 종목은 `expected=390`, `archived=5`, `censored`로 남고 성과 분모에서 제외됐다.
- 장마감 감사행 누락과 미완료 봉을 가리키는 후보 입력은 각각 exit 2로 거부됐으며 전략 SQLite나 metrics를 만들지 않았다.
- 실제 진행 중인 `20260714_forward_orb_premarket_actual_key` 세션은 최종 수동 QA 시점에 watch 182회 대비 랭킹 coverage cycle 70회, watch 실패 170회, retry audit 31회, 후보 입력 audit 16회였고 보고된 입력 147건과 SQLite 219건도 일치하지 않았다. 장마감 metrics 감사도 없어서 exit 2로 거부됐다.
- CLI `--help`, 합성 happy path, 실제 불완전 세션 오류 경로를 직접 실행했다.
- 전체 pytest 444개, 전체 Ruff lint, 전체 basedpyright가 통과했다. 변경 6개 Python 파일의 Ruff format과 no-excuse 검사는 위반 0건이다.

합성 거래는 실행 경로 QA일 뿐 수익성 표본이 아니다. 실제 날짜가 입력·분봉·장마감 게이트를 통과한 뒤에도 최소 60거래일·100건과 동일 포트폴리오 비교 전에는 paper 전진검증 후보로만 유지한다.
