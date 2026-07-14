# Codex 작업 시작점

## 프로젝트 목표

자동주문 없이 미국 급등주 후보를 관찰하고, 검증된 전략에 한해 진입가·손절가·목표가와 근거를 실시간 paper 의견으로 제공한다.

## 현재 상태

- KIS 읽기 전용 인증·랭킹·분봉 연결 완료
- KIS 주간거래 `BAQ/BAY/BAA`를 프리마켓·정규장과 분리한 원시 랭킹 forward 수집 완료
- 매 cycle KIS 원시 랭킹 행·출처·선택 여부 CSV 누적
- 선택 후보 완료 정규장 1분봉과 최초 관찰 시각 SQLite 영속화
- 최초 선택 후보의 당일 watchlist 유지, 랭킹 탈락 후 신규 신호 없는 추적
- 스캔 다음 1분봉 시가·완료 세션만 사용하는 forward outcome·임계값 진단 CLI
- ORB 81개 인접값·편도 5/10/20bp·최대 10포지션 전진성과 CLI
- `--strategy vwap_reclaim`으로 ORB와 섞지 않는 첫 눌림목 VWAP reclaim paper 실행
- `--strategy hod_breakout`으로 첫 HOD·2~8봉 base·거래량 재확대 paper 실행
- `--strategy gap_and_go`로 첫 5분 갭 지속·실패 분류 paper 실행
- 모든 KIS 전략은 NYSE 공식 현재 거래정지·호가·spread·슬리피지 위험 게이트를 포트폴리오 선정 전에 통과
- 전체 위험판정 모집단 보존과 spread·slippage·왕복비용 27개 인접값별 최대 10개 재선정 CLI
- 전체 위험판정 후보의 volume·ADV 보존과 등락률·가격·거래대금·volume/ADV 81개 조합별 재선정 CLI
- 현재 거래일의 최신 완료 봉만 신규 추천에 사용
- 과거 봉은 워밍업에만 사용해 과거 추천 역생성을 차단
- 시장 폐장·데이터 지연·호가 없음 상태에서 추천 차단
- 종목별 마지막 처리 봉을 저장하고 재시작 시 새 완료 봉만 처리
- 날짜별 영속 runner가 공급자 부분 오류를 실패 cycle로 감사하고 다음 cycle은 계속 실행
- 정규장 종료를 매 cycle 전에 다시 확인해 폐장 뒤 호출 중단
- NYSE 공식 2026~2028 휴장·조기폐장 반영, 미게시 연도 fail-closed
- 추천 ID 기반 SQLite immutable outbox와 JSONL·한국어 카드 projection
- 정규장 종료 시 마지막 완료 봉 가격으로 열린 추천 same-day `time_exit`
- paper 종료 거래의 5/10/20bp 비용·연도별 결과·bootstrap CI·fallback 비율 대시보드 구현
- 2026-07-13 실제 KIS 폐장 재검증에서 후보 3개, 당일 정규장 분봉·추천 0개 확인
- 2026-07-13 실제 KIS 폐장 위험 표본 163개와 27개 인접값 분석 저장, 폐장 호가 결손으로 식별력 없음 판정
- 같은 폐장 표본의 volume·ADV 163개를 완전 저장하고 81개 스캐너 조합을 재선정했지만 후행성과·opening gap은 데이터 결손 유지
- 실제 키 2회 QA에서 랭킹 1,200행·관찰 시각 2개 누적 확인
- 주문·잔고·계좌 API 없음

## 다음 우선순위

1. 미국 정규장에서 영속 runner를 실행해 최소 3개월 paper 표본 누적
2. 추천 카드 외부 전송 어댑터 연결
3. ORB·VWAP·HOD 전략의 정규장 forward paper 표본을 전략별로 분리 축적
4. 2029년 일정 게시 또는 임시 휴장 공지 시 캘린더 갱신

## 시작 전 확인

- `AGENTS.md`의 메모리·보안·주문 금지 규칙을 지킨다.
- `docs/runtime_audit.md`의 인과성 결함과 수정 내역을 읽는다.
- 실시간 작업 전 `uv run pytest -q`를 실행한다.
- API 키·토큰을 프롬프트·로그·코드·리포트에 출력하지 않는다.
- KIS는 전체시장 백테스트 원천이 아니라 forward paper 시세원으로만 사용한다.

## 새 작업용 요청문

```text
이 프로젝트의 README.md, CODEX_START_HERE.md, AGENTS.md와 docs/runtime_audit.md를 먼저 읽어줘.
현재 KIS 읽기 전용 paper 추천 에이전트를 이어서 개발하되 자동주문은 추가하지 마.
사용자가 지정한 메시지 채널로 local outbox를 전달하는 어댑터를 구현하고 테스트·수동 QA까지 완료해줘.
```
