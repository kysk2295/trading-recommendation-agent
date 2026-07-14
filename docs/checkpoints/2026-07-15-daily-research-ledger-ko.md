# 일일 Shadow 연구 원장 체크포인트

작성 시각: 2026-07-15 KST

## 목적

장중 broker 체결 엔진을 더 확장하지 않고, 급등주 후보·전략 shadow 결과가 매일 같은 계약으로 축적되는 최소 연구 루프를 고정했다. 이 기록은 수익 보장이나 실거래 승인 문서가 아니다.

## 구현된 흐름

1. 정규장 종료 뒤 `run_paper_metrics.py`가 비용별 Paper 성과를 만든다.
2. metrics 성공 시에만 `run_daily_research_record.py`가 실행된다.
3. 세션 안에는 불변 JSON과 한국어 요약, 세션 상위에는 append-only `daily_research_ledger.jsonl`이 저장된다.
4. metrics와 연구 원장 종료코드는 각각 별도 CSV에 남는다.

원장은 가설·반증 조건, 전략·코드·데이터·평가기 버전, 정확한 파라미터·비용·포트폴리오 정책, 입력 파일 checksum, 편도 20bp 성과와 운영 incident를 함께 저장한다. 같은 입력의 재실행은 동일 record ID를 사용해 중앙 원장 중복을 막는다.

## 품질 및 승격 게이트

적격 forward day는 watch cycle과 랭킹 coverage cycle 수가 같고, 각 cycle에서 NAS/NYS/AMS의 상승률·거래량 6개 요청이 모두 성공하며, 실패 watch cycle이 없을 때만 센다. 불완전한 날은 성과를 보존하되 60일 게이트에는 포함하지 않는다.

전략 승격은 다음 조건을 모두 충족하기 전까지 금지된다.

- 최소 60 적격 거래일
- 최소 100 완료 shadow 거래
- broker Paper ledger 검증
- 날짜 단위 block bootstrap
- DSR/PBO 다중검정 진단
- 인접 파라미터 평탄성
- SIP 또는 동등한 전체시장 데이터 검증

현재 구현은 이 blocker를 기록할 뿐 자동 승격이나 Alpaca 주문을 수행하지 않는다.

## 검증 증거

- 변경 파일 Ruff 검사와 포맷 검사 통과
- basedpyright 0 errors, 0 warnings
- 전체 pytest 429개 통과
- CLI `--help` 종료코드 0, 잘못된 날짜 종료코드 2
- 완료 ORB shadow 거래 1건 fixture에서 적격일 1일·거래 1건·승격 금지 기록 확인
- 동일 명령 2회 실행 뒤 중앙 JSONL 1줄 유지 확인
- 7월 14일→15일 기록 뒤 14일을 다시 실행하는 순서 역전 회귀에서 수정 전 중앙 JSONL 3줄을 재현했다. 미래 날짜를 누적치에서 제외한 뒤 실제 CLI 재실행은 날짜별 2줄, 누적 적격일 1→2, 고유 record ID 2개를 유지했다.
- 전체 회귀는 수정 후 430개가 통과했다.

전체 저장소에는 이번 변경과 무관한 Ruff 포맷 차이 91개 파일이 남아 있어 변경 파일 범위로 포맷 게이트를 적용했다.
