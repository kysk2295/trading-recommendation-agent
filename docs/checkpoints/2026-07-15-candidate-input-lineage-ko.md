# KIS 후보 평가 입력 계보 체크포인트

작성 시각: 2026-07-15 KST

## 문제

`candidate_minute_bars`와 랭킹 snapshot은 후보·분봉의 최초 가용시각을 보존하지만, 실시간 추천 엔진이 실제 사용한 완료 일봉 문맥과 관측 spread를 하나의 입력으로 고정하지 않았다. 이 상태에서 장마감 challenger를 재생하면 현재 또는 다른 cycle의 전일 종가·평균 거래량·spread를 결합할 위험이 있다.

## 수정 계약

- 분봉 freshness, 유한 spread, 새 완료 봉과 완료 일봉 문맥이 모두 통과한 신규 신호 평가만 기록한다.
- `candidate_input_snapshots`에 거래소·종목·실제 관찰시각·최신 완료 봉 시각·전일 종가·20일 평균 거래량·spread를 저장한다.
- 기본키는 거래소·종목·관찰시각이며 재실행은 최초 행을 유지한다.
- 추적 전용 `follow()`와 일봉 문맥 실패는 신규 후보 입력으로 추정하지 않는다.
- 일일 연구 원장은 snapshot 수를 품질 계보에 포함한다.
- 각 watch child는 선정 수·snapshot 수·scan 완료 여부를 `candidate_input_cycles.csv`에 남긴다.
- 일일 적격 게이트는 후보 입력 cycle/watch cycle 일치, 모든 scan 완료, cycle 합계/SQLite 행 수 일치를 요구한다.
- 이 단계는 challenger 수익성을 계산하지 않는다. 이후 replay가 사용할 시점 고정 입력을 확보하는 단계다.

## 검증

- 동일 관찰 snapshot을 두 번 저장해 첫 실행 1행, 두 번째 실행 0행을 확인했다.
- HTTP wire fake와 실제 SQLite를 사용한 scanner 통합 테스트에서 전일 종가 10.00, 20일 평균 거래량 200,000, 실제 spread와 09:32 최신 완료 봉이 09:33:30 관찰시각으로 저장됐다.
- 일일 연구 CLI가 후보 입력 snapshot 1건을 구조화 품질 필드와 한국어 요약에 기록했다.
- 후보 입력 cycle 감사 writer와 감사 파일 누락 시 일일 적격 거부를 회귀 테스트로 고정했다.
- 전체 pytest 440개, Ruff, 변경 파일 format check, basedpyright와 no-excuse 검사가 통과했다.

## 실제 watcher 적용 결과

- `main` 반영 뒤 기존 watcher의 01:18 KST cycle을 추가 실행하지 않고 그대로 관찰했다.
- 실제 선택 후보 10종목에 대해 `candidate_input_snapshots` 10행이 생성됐다.
- 각 행은 12:17 ET 최신 완료 봉, 종목별 전일 종가·20일 평균 거래량과 당시 spread를 보존했다.
- 같은 cycle 종료코드는 0이었고 KIS 읽기 재시도 6건은 모두 최종 200으로 복구됐다.
- watcher 부모·Python 프로세스 RSS 합계는 약 38MiB로 10GiB 안전 한도보다 충분히 낮았다.
- 이 기능 도입 전 cycle에는 입력 snapshot이 없으므로 해당 과거 구간을 현재 값으로 보간하지 않는다.
- cycle 감사 기능도 장중 도입 전 행을 소급 생성하지 않으므로 이 거래일은 계속 비교 불가다.
- cycle 감사 반영 뒤 기존 01:26 KST watcher는 `selected_count=10`, `context_count=9`, `scan_completed=True`를 첫 행으로 저장했다.
- 같은 관찰시각 이후 SQLite에 실제 추가된 후보 입력도 9행으로 cycle 합계와 일치했다.
- 누락 1종목 BTAI는 완료 일봉 GET이 두 번 모두 500이어서 입력 snapshot을 만들지 않았고, HODO 추적 분봉도 두 번 모두 500이었다.
- KIS 읽기 재시도 7건 중 5건만 복구되고 2건이 최종 실패해 watch cycle 종료코드는 1을 유지했다.
- 따라서 `scan_completed=True`는 runner가 끝까지 감사행을 썼다는 뜻일 뿐 데이터 성공을 뜻하지 않으며, 기존 watch 실패 게이트와 함께 해당 cycle을 부적격으로 처리한다.

이 결과는 Paper 전진검증 데이터 계보이며 확정 수익 또는 전략 우위의 증거가 아니다. 다음 단계는 이 snapshot과 최초 관찰 분봉을 사용한 독립 challenger 장마감 replay다.
