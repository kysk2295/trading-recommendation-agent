# Codex 작업 시작점

## 프로젝트 목표

미국 급등주 후보를 시점 가용 데이터로 관찰하고, 검증된 전략의 추천과 Alpaca Paper 전진검증을 한 프로젝트에서 운영한다. 실제 자금 주문은 영구 금지한다.

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
- Alpaca Paper 계좌·미체결 주문·포지션 GET-only adapter 완료
- 실행 원장 Single Writer 잠금·append-only schema·계좌 fingerprint 결합 완료
- GET-only bootstrap과 fail-closed preflight 실제 빈 Paper 계정 검증 완료
- Paper 시장시계 GET과 `trade_updates` 인증·구독·Ping/Pong, reconnect별 고유 `connection_epoch` 완료
- 활성 WSS의 두 Pong 사이 REST·단일 원장 대사와 공개 의존성 주입 없는 활성 세션 전용 승인 상태기계 완료
- 브로커 주문·포지션·원장 intent 기반 부분체결 단일 노출, 기존 노출별 손절·최소비용 위험 재계산과 신규 수량 내부 산정 완료
- 2026-07-14 실제 Paper 계정에서 WSS 인증·구독·Pong과 활성 연결 내부 계좌·시계·미체결·포지션·원장 대사 통과
- 주문 제출·취소·청산 API는 보호청산·EOD 게이트 전까지 비공개

## 다음 우선순위

1. `trade_updates` 체결 이벤트 영속화와 부분체결 보호주문·취소·EOD 강제 평탄화·재시작 대사 구현
2. 위 게이트가 모두 통과한 뒤 ORB 한 전략만 Alpaca Paper POST pilot으로 연결
3. broker fill과 conservative shadow fill을 분리 누적하고 최소 60일·100건 전진검증

## 시작 전 확인

- `AGENTS.md`의 메모리·보안·paper-only 주문 경계를 지킨다.
- `docs/runtime_audit.md`의 인과성 결함과 수정 내역을 읽는다.
- 실시간 작업 전 `uv run pytest -q`를 실행한다.
- API 키·토큰을 프롬프트·로그·코드·리포트에 출력하지 않는다.
- KIS는 전체시장 백테스트 원천이 아니라 forward paper 시세원으로만 사용한다.

## 새 작업용 요청문

```text
이 프로젝트의 README.md, CODEX_START_HERE.md, AGENTS.md와 docs/runtime_audit.md를 먼저 읽어줘.
현재 Single Writer Alpaca Paper 기반을 이어서 개발해줘.
README의 다음 우선순위 1번인 trade_updates 체결 원장·부분체결 보호주문·취소·EOD 평탄화·재시작 대사를 TDD로 구현하되, 모든 청산 안전장치가 완성되기 전에는 주문 POST/DELETE를 공개하지 마.
```
