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
- 읽기 client는 GET-only를 유지하고 mutation adapter는 단일 Writer/current-epoch 운영 세션 내부에서만 사용
- entry·보호 OCO·cancel/EOD 평탄화 공개 운영 메서드는 모두 명시적 `PaperMutationArm`이 필수
- 체결된 parent intent 하나에 대한 보호 OCO와 안전계획의 cancel·exact-position flatten smoke CLI 실행 가능. 실제 Paper POST/DELETE 검증은 아직 0건
- 안전조치 smoke는 계획과 같은 REST snapshot에서 1 entry order·1 position·1 OCO·1 symbol 및 합산 100 USD를 mutation 전에 강제하고, mutation 뒤 current-epoch 대사 실패를 성공이나 일반 차단으로 축소하지 않음
- 부분체결 누적 수량이 기존 보호 OCO보다 커지면 schema v9 source-bound DELETE만 먼저 실행하고, terminal 대사가 끝난 다음 호출에서만 새 deterministic client ID와 exact 수량으로 replacement OCO를 제출
- 보호 OCO cancel·replacement는 현재 5초 REST/WSS·ACTIVE 계좌 대사·브로커/로컬 정규장·15:55 ET 이전 게이트를 요구하며 timeout·재시작·한 leg fill·양 leg 경합을 fail-closed 복구
- `LaneId`·서로 다른 intraday/swing/regime 실행정책·보수적 risk contract·manifest·전용 account binding·experiment scope·final daily snapshot 계약 구현
- execution schema v9를 유지한 채 별도 lane registry schema v1의 append-only Writer/query-only Reviewer reader와 무네트워크 bootstrap CLI 구현
- 일일 연구 원장은 schema v2 exact scope로만 표본을 누적하고 schema v1 row는 파일을 재작성하지 않은 채 역사적 intraday scope로 투영
- ORB intraday final snapshot producer와 GET/WSS-only CLI 구현. 장 종료·5초 freshness·flat broker·registry/execution/readiness account 결합·exact daily scope·execution hash가 모두 맞아야 append하며 replay는 1행 유지
- 독립 Reviewer와 별도 review ledger schema v1 구현. Reviewer는 query-only lane registry·exact daily record·adaptive JSON만 읽고 false-only 전략/주문 권한 권고를 append하며 replay는 1행, 근거 변경은 immutable conflict
- ORB 장후 forward-validation runner 구현. snapshot 성공 뒤에만 Reviewer를 실행하고 두 단계 audit·redacted aggregate report를 남기며 주문권한·자동 승격·scheduler 역할은 없음
- ORB watch의 기존 metrics→daily record→adaptive 체인에 opt-in scheduled lane 단계를 연결. 네 경로 all-or-none·ORB-only이며 upstream 성공 뒤에만 snapshot→Reviewer runner를 호출하고 실패는 watch에 전파
- armed entry·safety smoke는 하나의 intraday pilot risk contract를 공유하며 100 USD·10 USD·1포지션·30 USD·편도 20bp·risk fraction 1/3000을 유지
- armed entry CLI는 free-form 종목·가격·시각·수량을 받지 않고 query-only watch SQLite에서 현재 직전 완료 정규장 1분봉에 결합된 30초 이내 ORB `setup` 후보 정확히 하나만 1주 요청으로 투영한 뒤 credential·운영 세션을 연다
- 모든 Alpaca Paper 운영 CLI는 잡힌 실행 예외의 클래스명만 stderr·보고서에 남기고 원문 계좌·broker·경로 정보를 버림
- 첫 정규장 smoke의 GET-only 준비, exact current ORB 후보 선택, armed entry, 보호 OCO, timeout 복구, staged EOD 평탄화와 최종 flat 대사를 하나의 운영 런북으로 고정

## 다음 우선순위

1. 열린 정규장에서 축소 entry 1건 → 즉시 보호 OCO → WSS·REST·Account Activities·원장 대사 → armed safety cancel/flatten → open order 0·position 0 최종 대사를 한 smoke로 검증
2. 추가 부분체결이 실제 발생할 때 staged 보호 OCO cancel → terminal 대사 → 다음 호출 replacement를 같은 축소 한도에서 검증하되 체결을 억지로 만들지 않음
3. 적격 ORB watch에 scheduled lane 경로를 명시해 장후 snapshot·Reviewer exact replay와 blocker를 자동 누적하되 자동 승격은 계속 금지
4. 최소 두 lane champion 전에는 Portfolio Manager를 구현하지 않고, swing은 shadow-only·regime은 signal-only를 유지

## 시작 전 확인

- `AGENTS.md`의 메모리·보안·paper-only 주문 경계를 지킨다.
- `docs/runtime_audit.md`의 인과성 결함과 수정 내역을 읽는다.
- 실시간 작업 전 `uv run pytest -q`를 실행한다.
- API 키·토큰을 프롬프트·로그·코드·리포트에 출력하지 않는다.
- KIS는 전체시장 백테스트 원천이 아니라 forward paper 시세원으로만 사용한다.
- `docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md`를 순서대로 따르고, nonzero 단계나 stale 후보를 우회하지 않는다.

## 새 작업용 요청문

```text
이 프로젝트의 README.md, CODEX_START_HERE.md, AGENTS.md와 docs/runtime_audit.md를 먼저 읽어줘.
현재 Single Writer Alpaca Paper 기반을 이어서 개발해줘.
README의 다음 우선순위 1번인 축소 정규장 Paper 수명주기를 현재시점 게이트 아래 검증해줘. 장이 닫혀 있거나 안전조건이 부족하면 실제 mutation을 하지 말고, 구현된 lane registry 주위에 ORB finalized daily snapshot과 query-only Reviewer loop를 연결해줘.
```
