# 급등주 실시간 추천 에이전트 설계

## 목표

미국 전체 종목을 스캔하되 장전·장중 급등 조건을 만족한 종목만 분석하고, 조건부 추천에서 Alpaca Paper 전진검증까지 재현 가능한 원장으로 운영한다. 실제 자금 주문은 포함하지 않는다.

## 실행 흐름

```text
분봉·호가·뉴스 데이터 + NYSE 공식 현재 거래정지
→ 급등주 스캐너
→ 시장위험 게이트
→ 승인된 전략 플러그인
→ 리스크 엔진
→ 추천 카드
→ SQLite 이벤트 로그
→ SQLite immutable alert outbox
→ 사용자 알림
→ 장 마감 결과 판정
→ 비용 민감도·연도별 paper 성과 보고서
```

Paper 실행 경계는 다음과 같이 별도 계층으로 둔다.

```text
하나의 Operator Writer
→ 비차단 process lock
→ append-only execution ledger
→ Alpaca Paper broker adapter
→ broker ledger + conservative shadow ledger

Researcher·Developer·Reviewer
→ mode=ro ledger snapshot
→ 제안·검토 결과만 Writer에게 전달
```

## 책임 분리

- 데이터 공급자: 분봉, 전일 종가, 평균 거래량, 스프레드와 촉매를 동일한 시각 기준으로 제공한다.
- 급등주 스캐너: 갭, 당일 상승률, 상대거래량, 거래대금, 가격과 스프레드로 후보만 선별한다.
- 시장위험 게이트: active halt, 호가 결손·역전, 100bp spread, 편도 20bp 슬리피지 예비비를 합친 왕복비용 140bp 초과를 최대 포지션 선정 전에 차단한다.
- 전략 엔진: ORB, VWAP 첫 눌림목, HOD 돌파처럼 진입 조건만 판단한다.
- 리스크 엔진: 진입가, 손절가, 1R·2R 목표, 포지션 한도를 계산하고 과도한 위험은 거절한다.
- 추천 엔진: 조건부 추천을 생성하고 체결 여부, 손절, 목표, 장 마감 종료를 추적한다.
- 알림 outbox: 추천 ID별 최초 카드 JSON·한국어 문구를 한 번만 저장하고 projection 파일을 재생성한다.
- 성과 집계기: 실제 `active` 이벤트 뒤 terminal exit가 있는 paper 거래만 추출하고 편도 5/10/20bp 비용, PF·승률·평균·누적·MDD·bootstrap CI와 fallback 비율을 기록한다.
- 연구 게이트: 과거 백테스트와 실시간 paper 결과를 통과한 전략만 활성화한다.

## 전략 승격 단계

```text
research → historical_pass → paper → approved → suspended
```

- `research`: 개발·백테스트 중이며 추천에 사용하지 않는다.
- `historical_pass`: 비용·기간분리·과최적화 검사를 통과했다.
- `paper`: 실시간 데이터에서 가상 추천만 기록한다.
- `approved`: 사용자에게 실시간 의견을 표시할 수 있다.
- `suspended`: 최근 PF, 데이터 품질 또는 체결 가능성이 기준 아래로 떨어졌다.

## 강제 안전장치

- Alpaca Paper 외의 브로커 주문 API를 연결하지 않는다.
- Alpaca Paper POST/DELETE는 정규장·현재 완료 봉·order stream·위험승인·보호청산·EOD 평탄화가 모두 준비되기 전까지 공개하지 않는다.
- 실행 원장을 변경하는 프로세스는 하나만 허용하며 두 번째 Writer는 공급자 호출 전에 실패한다.
- 추천 생성 시각 이후의 가격과 거래량을 입력 특성으로 사용하지 않는다.
- 분봉이 없으면 ORB·VWAP·HOD 전략을 실행하지 않는다.
- 같은 봉에서 손절과 목표가 모두 닿으면 손절을 먼저 적용한다.
- 모든 상태 변경을 별도 이벤트로 저장하고 실패한 추천을 삭제하지 않는다.
- 스프레드, 거래정지, 데이터 지연, 위험폭 초과 시 추천하지 않는다.
- PIT free float가 없으면 low-float 값을 추정하지 않고 해당 필터를 제외 상태로 명시한다.

## 첫 MVP

첫 버전은 표준 CSV 분봉을 시간순으로 재생한다. 급등 스캐너와 5분 ORB를 실행하고 조건부 진입, 손절, 1R·2R 및 장 마감 결과를 SQLite에 저장한다. 실시간 공급자는 같은 입력 인터페이스를 구현해 이후 교체한다.

## Alpaca Paper 실행 기반

2026-07-14부터 다음 기반이 구현되어 있다.

- 정확한 `https://paper-api.alpaca.markets`만 허용하고 live 도메인은 HTTP 전에 거절
- Paper 전용 자격증명 파일과 exact mode 600 검사
- cross-origin 인증 헤더 유출을 막기 위한 redirect 비활성화
- 계좌 ID·계좌번호 대신 로컬 SHA-256 fingerprint만 원장에 결합
- WAL·외래키·event dedupe·UPDATE/DELETE 금지 trigger가 있는 실행 원장
- 비차단 파일 잠금으로 한 개 Writer만 허용하고 다수 reader는 `mode=ro` 사용
- 주문 symbol·side·quantity·limit·TIF·extended-hours까지 비교하는 fail-closed 대사
- Alpaca Paper `/v2/clock`과 정확한 `wss://paper-api.alpaca.markets/stream`만 사용하는 주문 스트림 control plane
- binary/text 인증 응답 검증, `trade_updates` 구독 승인, RFC 6455 Ping/Pong과 연결별 무작위 `connection_epoch`
- 수신한 text/binary frame의 정확한 원문 BLOB·wire kind·수신시각·connection epoch를 먼저 append-only 확정하고, 검증 결과를 별도 disposition으로 기록하는 raw-first 경계
- 프로세스가 raw commit 뒤 중단되어도 미분류 receipt를 SQLite 수신 순서대로 재처리하며, protocol·계좌·intent·주문 불일치는 정상 event가 아니라 quarantine으로 보존하는 재시작 복구
- 두 heartbeat 사이 계좌·open 주문·미해결 intent별 주문·최근 7일 주문·포지션을 GET하고 하나의 recovery snapshot으로 확정하는 REST 대사
- REST aggregate 체결량과 개별 execution event를 분리하고, execution 누락·가격 불일치·수량 회귀·terminal 상태 변경·immutable 충돌을 신규 주문 차단 사유로 보존하는 projection
- v3 스키마의 table·trigger·unique index 정의와 저장 payload·receipt·recovery hash를 읽을 때도 다시 검증하는 손상 감지
- 스트림의 두 Pong 사이에 계좌·미체결·포지션·시장시계를 GET하고 단일 SQLite 원장 snapshot과 대사·포트폴리오 집계를 끝내는 활성 세션 전용 승인 경계. 공개 factory에는 provider·clock 주입 인자가 없다.

현재 외부 API 표면은 시장시계·계좌·주문·포지션 조회 GET과 주문 스트림 인증·구독·Ping뿐이다. 주문 POST/PATCH/DELETE 메서드는 존재하지 않는다. REST aggregate가 누락 체결을 발견하면 주문 상태·누적량은 복원하지만 존재하지 않는 개별 execution을 합성하지 않으며, 상세 체결은 불완전 상태로 남긴다. 일반 protocol quarantine은 이후 일관된 REST 복구로 해소할 수 있지만 immutable 충돌은 account activity나 수동 감사 근거 없이 자동 해소하지 않는다.

주문 변경 기능을 열기 전 P0 경계는 **하나의 장수명 WSS 소유자**가 ingestion과 admission을 함께 직렬화하는 것이다. 현재 recovery probe와 readiness probe는 각각 안전한 읽기 전용 연결을 열 수 있지만, Pong은 이벤트 처리 high-water나 replay cursor가 아니다. 따라서 실제 주문 admission 시점에는 current epoch recovery가 끝난 뒤 ledger generation이 변하지 않았음을 같은 직렬화 경계 안에서 확인해야 한다. 이 경계와 account activity 기반 fill/correction/bust 복구, 보호청산·kill switch·EOD 평탄화가 완성될 때까지 POST/PATCH/DELETE는 닫아 둔다.

신규 주문 승인 상태기계는 다음 순서를 고정한다.

```text
활성 WSS 첫 Pong
→ 같은 connection_epoch의 두 Pong 사이 REST·단일 원장 대사,
   응답 수신시각과 완전한 포트폴리오 admission
→ Alpaca clock 5초 이내 + is_open
→ NYSE 로컬 달력 정규장 + 폐장 30분 전 이전
→ 해당 종목의 방금 완성된 정확한 정규장 1분봉 + 최초 관찰 뒤 생성된 intent
→ trade_updates 인증·구독 순서 + 5초 이내 Pong
→ account ACTIVE, 당일손실, 중복종목, position/pending slot, buying power,
   기존·신규 각 종목 위험·명목금액, 총 계획위험·gross exposure
→ APPROVED
```

검사 순서를 건너뛰지 않으며 runtime admission의 대사·완전성 실패를 가장 먼저 차단한다. 정규장 첫 1분이 아직 완성되지 않은 09:30대에는 09:29 장전봉을 current bar로 인정하지 않는다. 포트폴리오는 브로커 주문·포지션과 원장 intent를 직접 결합해 만든다. 부분체결은 체결 포지션과 남은 주문 명목금액을 합친 한 노출로 세고, 미체결 주문이 사라진 완전체결 포지션은 로컬 `fill` 이벤트 증거까지 요구한다. 포지션 market value는 0·수량과 반대 부호면 불완전으로 차단하고, 유효해도 원장 진입가 기준 명목금액보다 작게 집계하지 않는다. 기존 활성 노출의 계획위험은 현재 거래당 예약 한도와 원장 수량 전체의 손절거리·설정된 최소비용 위험 중 큰 값이며, 기존 노출 각각에도 종목당 USD 75·USD 6,000 한도를 적용한다. 하나라도 결합할 수 없으면 `IncompletePaperPortfolio`로 차단한다. 신규 수량은 외부 계산값을 받지 않고 손절거리·스프레드·왕복 최소 20bp 비용을 포함해 내부 산정한다. 기본 하드 한도는 거래당 USD 75, 종목당 USD 6,000, 동시 3개, 총 계획위험 USD 225, gross exposure USD 18,000과 conservative equity 중 작은 값, 당일손실 정확히 −USD 300부터 중단이다.

2026-07-14 실제 Paper 계정 QA에서 v1 원장을 v3로 안전하게 migration한 뒤 bootstrap, recovery, readiness를 순서대로 실행했다. recovery는 WSS 인증·`trade_updates` 구독·두 heartbeat 사이 계좌·주문·포지션 GET snapshot을 저장했고, 당시 계정에는 주문·포지션·raw receipt가 없어 execution 상세가 완전한 빈 상태로 확인됐다. readiness도 스트림·REST·원장·포트폴리오 대사를 통과했다. broker clock이 열려 있어도 후보 current bar와 실제 주문 intent를 넣지 않았으므로 신규 주문 승인은 미평가로 남겼다. 모든 호출은 WSS와 REST GET뿐이었고 이 probe 결과는 세션 종료 뒤 승인에 재사용되지 않는다.

구현 계약은 Alpaca의 [WebSocket streaming 문서](https://docs.alpaca.markets/us/docs/websocket-streaming), [Market Clock 문서](https://docs.alpaca.markets/us/v1.1/reference/getclock-1), [Paper Trading 설명](https://docs.alpaca.markets/us/docs/paper-trading)을 기준으로 한다. Alpaca 문서가 애플리케이션 heartbeat 주기나 reconnect replay를 보장하지 않으므로 Pong만으로 주문 상태를 복구했다고 간주하지 않고 매 연결 세대마다 REST 대사를 다시 요구한다.

## KIS 실시간 시세 연결

2026-07-13부터 한국투자증권 Open API를 읽기 전용 시세 공급자로 연결했다.

```text
NASDAQ·NYSE·AMEX 상승률/거래량 랭킹
→ 등락률 4% 이상·가격 1~200달러·거래대금 50만달러 이상
→ 후보별 최근 정규장 1분봉 역조회
→ 최근 20개 완료 일봉으로 전일 종가·평균 거래량 계산
→ 관찰 시각보다 최소 1분 전에 끝난 당일 정규장 봉만 선택
→ 과거 봉은 갭·RVOL·시초 범위 워밍업, 최신 완료 봉 하나만 신규 신호 평가
→ paper 추천 및 감사 로그 저장
```

- 실전 키는 실전 **시세 조회**에만 사용한다. 주문·잔고·계좌 엔드포인트는 코드에 없다.
- 앱 키와 시크릿은 `~/.config/trading-agent/kis.env`에 권한 `600`으로 저장한다.
- 새 접근 토큰은 `~/.cache/trading-agent/`에 권한 `600`으로 캐시하고 만료 전에 재사용한다.
- ORB는 미국 동부시간 09:30부터 정확히 이어지는 분봉이 모두 있을 때만 신호를 낸다. 장전 또는 늦게 시작된 데이터로 시초 범위를 대신 만들지 않는다.
- 장이 닫혀 현재 매수·매도 호가가 없으면 스프레드를 계산하지 않고 추천을 차단한다.
- 현재 랭킹으로 선택한 종목의 과거 분봉에서 뒤늦게 추천을 만들지 않는다. 랭킹 관찰 전 봉은 상태 계산에만 사용하고 추천 시각은 최신 완료 봉보다 앞설 수 없다.

단발 진단 실행 파일은 `run_kis_paper_scan.py`다. 기본 실행은 세 거래소의 상승률·거래량 랭킹을 한 번 조회하고 상위 3개 후보를 분석한다. 날짜별 영속 감시는 `run_kis_paper_watch.py`가 같은 SQLite를 재사용하며 60초 간격으로 최대 390회 순차 실행한다.

각 cycle이 조회한 상승률·거래량 랭킹 원시 행은 `kis_ranking_snapshots.csv`에 덮어쓰기 없이 누적한다. 관찰 시각, 랭킹 출처, 거래소, 원천 순위, 가격·등락률·호가·거래량·거래대금·평균 거래량과 실제 선택 여부를 저장하므로 이후 임계값 인접값과 후보 품질을 재현할 수 있다. 단, 이는 KIS 랭킹 API가 노출한 상위 표본이며 미국 전체 종목의 point-in-time 원시 모집단은 아니다.

선택 후보의 완료 정규장 분봉은 같은 SQLite의 `candidate_minute_bars`에 거래소·종목·시각 기본키로 저장한다. OHLCV·거래대금·한국 및 거래소 시각과 `first_observed_at`을 보존하고 반복 조회는 `INSERT OR IGNORE`로 최초 관찰값을 유지한다. 장이 닫혔거나 관찰 거래일과 다른 과거 분봉은 저장하지 않는다.

정규장에서 최초 선택된 종목은 `tracked_candidates`에 뉴욕 거래일별로 보존한다. 이후 현재 상위 후보에서 빠진 종목은 `follow()` 경로로 분봉을 계속 저장한다. 열린 추천이 있으면 새 완료 봉으로 상태만 갱신하고, 열린 추천이 없으면 ORB 조건이 보여도 신규 추천을 생성하지 않는다. 따라서 현재 스캐너 선정과 과거 선정 종목 추적이 분리된다.

랭킹 CSV는 종목 키의 선택 여부 `selected`와 실제 필터 입력 행 `selection_input`을 분리한다. 구형 행은 실제 입력 출처를 추정하지 않고 `selection_input`을 빈 값으로 migration한다. 랭킹 응답이 끝난 뒤 관찰 시각을 기록하며 forward 분석은 종목·거래일 최초 `selection_input=True`만 사용한다.

`run_scanner_forward_metrics.py`는 관찰 뒤 다음 완전한 1분봉 시가를 진입 proxy로 사용한다. 공식 close 직전 봉까지 1분 간격이 모두 이어진 경로만 5/15/30분 수익, EOD, MFE, MAE와 편도 5/10/20bp 결과에 포함한다. 갭·거래대금 4×4 인접값 격자와 bootstrap CI를 만들지만 KIS 상위 랭킹 표본의 forward 진단일 뿐 전략 백테스트로 승격하지 않는다.

`run_orb_forward_metrics.py`는 랭킹의 `selection_input=True` 시각과 각 분봉의 `first_observed_at`을 분리한다. 같은 scan cycle에서 실제 분봉 조회가 끝난 시각을 신호 가용시각으로 사용하고 그 다음 완전한 1분봉부터 조건부 진입을 허용한다. OR 1/5/15분, 거래량 1.0/1.5/2.0배, 손절폭 0.75/1.0/1.25배, 목표 1R/2R/3R을 전수 비교하며, 신호 시점 상승률·거래대금 순으로 동시 최대 10포지션을 사전 배정한다. 완료 세션만 PF·승률·평균·누적·MDD·bootstrap CI와 연도별 결과에 포함한다.

추천 엔진은 `IntradayStrategy` 계약을 받아 ORB, VWAP reclaim, HOD breakout, Gap-and-Go를 독립 실행한다. `vwap_reclaim`은 정규장 HLC3×거래량의 누적 VWAP을 계산하고, 종가가 VWAP 위로 확장된 뒤 첫 touch가 발생하고 이후 상승 VWAP 위에서 거래량 재확대·pullback 고가 돌파가 확인될 때만 신호를 낸다. `hod_breakout`은 첫 3% HOD 뒤 2~8봉 base와 1.5배 거래량을 요구하고 첫 5bp 돌파 시도를 봉 종료 후에만 판정한다. `gap_and_go`는 첫 5분 저가·종가·VWAP만으로 continuation·failure·neutral을 한 번만 분류하고 늦은 후보 도착은 중립으로 종료한다. VWAP과 HOD 모두 실패한 첫 패턴 뒤 두 번째 패턴을 선택하지 않아 사후 선택을 차단한다.

영속 감시는 종목별 마지막 처리 봉을 `bar_checkpoints`에 저장한다. 재시작했을 때 이미 처리한 봉에서는 신호를 다시 만들지 않고, 놓친 새 봉은 기존 추천의 진입·무효·손절·목표 상태 갱신에 사용한다. 신규 ORB 추천은 가장 최신인 새 완료 봉에서만 평가하고 같은 종목·전략·거래일에는 최대 1개만 허용한다. 각 실행 주기의 종료코드와 성공·실패 상태는 `watch_cycles.csv`에 즉시 추가하며 매 cycle 직전에 정규장 여부를 다시 확인한다.

신규 추천은 `alert_outbox`에 추천 ID 기본키로 저장한다. JSONL·Markdown 파일이 삭제돼도 SQLite 원본에서 복원하며 같은 추천을 다시 queue하지 않는다. KIS 경로는 스캔 직전 5분 이내 생성된 추천만 최초 queue해 기존 DB의 과거 추천을 현재 알림처럼 보내지 않는다. 외부 메시지 어댑터는 이 outbox를 읽기 전용으로 소비해야 한다.

정규장 close가 지나면 watch는 열린 추천을 해당 거래일의 `time_exit`으로 바꾸고 보고서를 다시 쓴다. 종료 가격은 마지막 처리 완료 봉 close이며 실제 MOC가 아니므로 이벤트 메모에 봉 시각을 남기고 성과 집계에서 별도 체결 품질로 다뤄야 한다.

거래소별 상승률·거래량 랭킹 요청은 독립적으로 수집한다. 특정 요청이 공급자 오류로 실패하면 나머지 성공 그룹을 후보·shadow 전략 평가에 사용하되, `kis_ranking_request_coverage.csv`와 한국어 scan 보고서에 누락 범위를 기록하고 cycle 종료코드는 실패로 유지한다. 따라서 일시적인 한 거래소 장애가 모든 전략 관찰을 끊지는 않지만 부분 표본을 완전한 미국시장 모집단으로 해석할 수도 없다.

KIS 랭킹·분봉·일봉·현재가상세는 모두 읽기 전용 GET이다. 500/502/503/504만 80ms 뒤 정확히 한 번 다시 요청하고, 두 번째 오류 응답과 429는 추가 시도 없이 기존 오류 경로로 전달한다. 첫 요청의 일시적 오류가 두 번째 요청에서 실제 성공한 경우에만 해당 입력을 사용하며, 반복 실패는 observation·coverage·cycle 비영 종료코드에 그대로 남긴다. 이 재시도는 주문 API나 상태 변경 요청에 적용되지 않는다.

각 scan cycle은 재시도가 없어도 `kis_read_retry_cycles.csv`에 한 행을 남긴다. 재시도가 있으면 `kis_read_retry_events.csv`에 endpoint path, 거래소, 종목, 최초·최종 HTTP status와 결과만 기록하며 인증 header와 token은 기록하지 않는다. 일일 연구 원장은 watch cycle과 retry cycle 수가 일치해야 품질 적격으로 판정하고, 두 감사 CSV가 존재하면 checksum과 데이터 버전에 포함한다. 복구 성공은 입력 누락이 없으면 그 자체로 날짜를 탈락시키지 않지만 운영 incident로 남는다.

watch는 공식 정규장 종료 뒤 `run_paper_metrics.py`를 한 번 실행해 `paper_metrics/`와 `post_session_metrics_cycles.csv`를 만든다. 장중에 cycle 수를 줄여 종료한 실행은 이 단계를 건너뛴다. 이 자동화는 broker 주문 처리와 독립적인 shadow 연구 경로이며 미종료 추천·미체결 무효화는 거래 성과에서 제외한다.

`run_paper_metrics.py`는 여러 날짜별 SQLite를 읽고 추천 ID를 중복 제거한다. 미체결 무효화와 미종료 추천은 제외하며, 누적수익과 MDD는 거래 순차 복리 proxy로만 계산한다. 평균수익 CI는 개별 거래가 아니라 `exit_at`을 뉴욕 거래일로 정규화한 날짜 블록을 재표본화해 같은 날 거래의 의존성을 보존한다. 거래일 블록이 2개 미만이면 가짜 정밀도를 피하기 위해 CI를 공란으로 둔다. 이는 최대 10포지션 일별 포트폴리오 백테스트가 아니며, 작은 paper 표본의 block-bootstrap CI도 전략 승격 근거로 단독 사용하지 않는다.

metrics가 성공하면 watch는 `run_daily_research_record.py`를 이어서 실행하고 종료코드를 `post_session_research_cycles.csv`에 별도로 기록한다. 이 CLI는 세션 산출물 SHA-256, 코드·데이터·평가기 버전, 정확한 전략 파라미터·비용·포트폴리오 정책, 편도 20bp 결과, 데이터 품질 incident와 누적 적격 거래일·완료 거래 수를 불변 JSON과 append-only JSONL로 저장한다. 같은 record ID를 재실행해도 중앙 원장에는 중복 추가하지 않는다.

누적치는 같은 전략 버전에서 기록 대상 거래일보다 앞선 날짜만 사용한다. 따라서 이후 거래일이 원장에 추가된 뒤 과거 세션을 재생해도 미래 날짜가 과거의 누적치와 record ID에 들어가지 않으며, 동일 입력은 중복 행을 만들지 않는다.

적격 forward day는 watch cycle마다 거래소 3곳×랭킹 2종의 6개 요청이 모두 성공하고, coverage cycle 수와 watch cycle 수가 같으며, 실패 watch cycle이 없을 때만 증가한다. 승격은 최소 60 적격 거래일·100 완료 거래뿐 아니라 broker paper ledger, DSR/PBO, 인접 파라미터 평탄성, SIP 검증이 모두 충족돼야 한다. 평가기 버전이 다른 원장은 누적 거래일·거래 수에 섞지 않는다. 현재 경로는 연구 기록만 만들고 전략 상태를 자동 변경하거나 주문을 제출하지 않는다.

## 현재 범위의 한계

KIS 랭킹은 거래소 전체 종목의 원시 스트림이 아니라 API가 반환하는 상위 후보 목록이다. 따라서 현재 구현은 미국 전체 시장을 완전히 열거하는 스캐너가 아니라 **상승률·거래량 상위 후보 스캐너**다. 영속 상태 추적, NYSE 공식 2026~2028 휴장·13:00 조기폐장과 마지막 완료 봉 기반 장 마감 결과 판정은 구현됐다. 게시 범위 밖 연도는 fail-closed이며, 임시 휴장 공지와 실제 MOC·15:59 체결 검증은 아직 운영 승격 전 게이트다. 완전한 전체시장 감시와 3년 역사 백테스트에는 PIT 종목 마스터, 전체시장 분봉, 과거 NBBO, halt/LULD 자료가 별도로 필요하다.

실시간 후보 선정에는 NYSE의 공식 현재 거래정지 CSV를 추가로 사용한다. 스키마 변경·HTTP 실패는 cycle 실패로 처리하며, 선정·포트폴리오 한도 제외·위험 제외를 포함한 전체 판정 모집단을 `market_risk_screen.csv`에 누적한다. `run_market_risk_sensitivity.py`는 이 모집단을 spread·slippage·왕복비용 27개 조합마다 다시 필터링한 뒤 최대 10개를 재선정한다. 이는 후보 보존율 진단이며 현재 halt 차단일 뿐 3년 역사 halt/LULD 커버리지나 수익성 검증을 대체하지 않는다.

같은 CSV에는 스캔 시점 누적 거래량·ADV·volume/ADV를 저장한다. `run_scanner_candidate_sensitivity.py`는 등락률·최대가격·거래대금·volume/ADV 81개 조합마다 위험 통과 전체 후보를 다시 정렬해 최대 10개를 선정한다. 기존 `scanner_threshold_summary.csv`는 baseline 선택 종목의 사후 필터이므로 전체 후보 비교에 사용하지 않는다. KIS 랭킹에 전체 후보 시가가 없어 opening gap은 결손으로 유지한다.
