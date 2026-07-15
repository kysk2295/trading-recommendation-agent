# 미국 급등주 자율 Paper Trading Research OS

미국 급등주를 장전·장중에 시점 가용 데이터로 탐색하고, 전략 연구부터 실시간 Alpaca paper 주문, 장후 평가와 다음 가설 생성까지 하나의 프로젝트에서 반복하기 위한 연구 시스템이다.

> **현재 상태:** 분봉 수집·급등주 스캐너·ORB/VWAP/HOD/Gap-and-Go 추천·인과성 감사·과거 및 forward 평가에 더해 Alpaca Paper 시장시계·계좌·주문·포지션·Account Activities FILL GET, `trade_updates` 스트림 인증·구독·Ping/Pong, 단일 Writer 원장과 fail-closed 주문 승인 게이트까지 구현되어 있다. 수신 frame은 text/binary 원문 BLOB을 먼저 확정한 뒤 분류하며, 재시작 시 미분류 receipt를 원래 순서로 복구하고 매 연결 세대에서 REST 주문 snapshot·개별 FILL activity·nested 보호 OCO와 대사한다. 모호한 entry/OCO/cancel mutation은 deterministic client order ID 또는 broker order ID로 직접 GET하며, 정확한 targeted 증거가 없으면 재전송하지 않는다. 통합 운영 세션은 한 Writer lease와 한 WSS 안에서 current-epoch 복구·승인·Paper mutation·사후 대사를 직렬 실행한다. 부분체결 수량이 기존 보호 OCO보다 늘어나면 source-bound cancel만 먼저 실행하고 terminal 대사 뒤 다음 호출에서 새 client ID와 exact 수량으로 replacement OCO를 제출한다. 별도 append-only lane registry는 세 manifest·전용 Paper account binding·사전등록 experiment scope·final daily snapshot 계약을 보존하고 Reviewer용 query-only reader를 제공한다. 일일 연구 원장은 schema v2에서 exact lane scope로만 표본을 누적한다. 신규 진입, 보호 OCO 수명주기, cutoff·kill switch·EOD cancel/flatten은 모두 정확한 arm 객체가 필요한 축소 smoke CLI로만 열렸고 실제 정규장 Paper mutation은 아직 0건이다.

실제 자금 거래는 목표가 아니다. 앞으로 추가되는 실행 코드는 `https://paper-api.alpaca.markets`에만 연결하며 Alpaca live endpoint, 실계좌 키와 실제 주문 경로는 프로젝트에서 차단한다.

## 최종 목표

```text
과거 연구와 실패 원장
→ 장전 급등주 스캐너
→ 정규장 ORB·VWAP reclaim·HOD challenger
→ USD 30,000 위험 커널
→ Alpaca Paper 주문
→ Broker 체결 원장 + 보수적 Shadow 체결 원장
→ 장후 PF·승률·평균수익·누적수익·MDD 평가
→ 전략 자동 승격·강등
→ 주간 새 가설 생성
```

하나의 지속형 에이전트가 다음 네 모드를 순환한다.

- `Researcher`: 실패와 시장 변화에서 반증 가능한 가설 생성
- `Developer`: 같은 Strategy API로 historical/live 코드를 구현하고 테스트
- `Reviewer`: 인과성·비용·OOS·bootstrap·다중검정·인접 파라미터 안정성 판정
- `Operator`: 장중 paper 실행, 대사, kill switch와 장후 보고

에이전트는 paper 환경 안에서 전략 생성·실험·승격·중단을 자동 수행한다. 과거 원장 삭제, 실거래 활성화와 안전선 우회는 허용하지 않는다.

### Single Writer, Multiple Readers

- 실행 원장과 향후 broker paper 상태를 변경하는 프로세스는 하나뿐이다.
- Writer는 canonical SQLite 경로의 `.writer.lock`을 비차단으로 획득한 뒤에만 스키마 생성과 계좌 결합을 수행한다.
- 두 번째 Writer는 자격증명 또는 공급자 호출 전에 즉시 실패한다.
- 연구·리뷰·보고 작업은 SQLite `mode=ro`와 `query_only` 연결만 사용한다.
- 같은 intent 또는 broker event key의 immutable 필드가 다르면 재시도로 간주하지 않고 차단한다.
- 장중 운영 세션은 하나의 WSS에서 `trade_updates` 수신과 주문 admission을 직렬화하고, current-epoch 복구 checkpoint 전후의 원장 세대를 비교한다.
- 읽기 전용 Alpaca client는 GET 메서드만 공개한다. 별도 mutation adapter는 단일 Writer/current-epoch 운영 세션 안에서만 열리며 entry·보호 OCO·cancel/EOD 평탄화 공개 메서드는 모두 정확한 `PaperMutationArm`을 요구한다.
- execution schema v9와 분리된 lane registry schema v1은 manifest·account fingerprint binding·experiment scope·final snapshot을 append-only 저장하고 `mode=ro`/`query_only` Reviewer reader를 제공한다. registry는 주문 메서드가 없다.

## 하나의 프로젝트, 세 연구 Lane

| Lane | 포함 연구 | 실행 권한 | 상태 |
|---|---|---|---|
| `intraday_momentum` | ORB, 첫 눌림 VWAP reclaim, HOD breakout, Gap-and-Go | 정규장 Alpaca Paper, 전용 account binding 필수 | manifest 1.0.1 등록 |
| `swing_momentum` | Regend, RVOL, 신고가·모멘텀 | shadow-only 다중세션 상태기계 | manifest 1.0.1 등록 |
| `market_regime` | VIX, VIX3M, SKEW, SCR | signal-only, account binding 금지 | manifest 1.0.0 등록 |

세 lane은 같은 실험 원장과 버전 체계를 사용하지만 성과를 사후 혼합하지 않는다. VIX 필터를 ORB에 추가하려면 `ORB baseline`과 `ORB + VIX regime`을 같은 기간·위험으로 비교하는 새 challenger를 등록해야 한다.

## 승인된 Paper 운용 계약

- 기준 자본: USD 30,000 로컬 가상 equity
- 레버리지: 1.0배
- 거래당 초기 위험: 최대 USD 75 또는 현재 conservative equity의 0.25% 중 작은 값
- 종목당 명목금액: 최대 USD 6,000
- 동시 포지션: 최대 3개
- 일일 kill switch: 실현손익과 보수적 미실현손익 합계 −USD 300
- 장전: 스캔만 수행하고 주문 금지
- 정규장: paper 주문만 허용
- 폐장 30분 전 신규 진입 중단, 폐장 5분 전 전량 청산
- 오버나이트 포지션: 0

Alpaca paper 체결은 실제 호가 잔량·시장충격·지연 슬리피지·주문 대기열을 완전히 재현하지 않는다. 따라서 broker paper fill과 보수적 shadow fill을 별도 원장으로 계산하고 둘 다 통과해야 Paper Champion으로 승격한다.

## 전략 자동 승격 기준

`IDEA → HISTORICAL → EXPERIMENTAL_PAPER → CHALLENGER → PAPER_CHAMPION ↔ SUSPENDED`

60거래일은 전략을 그대로 두고 기다리는 기간이 아니다. 모든 전략은 매일 독립 shadow로 동시에 평가한다. 최근 5 적격일·10거래에서 편도 20bp PF<0.75, 평균<0, 거래일 block-bootstrap 95% CI 상단<0이 모두 확인되면 조기중단하고, 10일에는 약한 edge를 진단하며, 20일·30거래부터 동일 위험 champion 비교 후보가 된다. 이미 성숙한 후보도 최근 5일의 같은 명확한 열화가 발생하면 즉시 `SUSPENDED` 권고를 받는다.

Paper Champion 최종 검토는 최소 60 적격 거래일·100건, 최근 60일 broker/shadow 양쪽 PF 1.15 이상, 편도 20bp 비용 후 평균수익 양수, 거래일 block-bootstrap 95% CI 하한 0 이상, 장전 시점 시장 국면 coverage 80% 이상·최소 2개 국면, 진입 시점 가격·갭·volume/ADV·거래대금 특성 coverage 80% 이상, DSR/PBO와 인접 파라미터 plateau를 모두 통과해야 한다. 10거래 이상인 시장 국면 또는 종목 특성 cohort의 PF<0.8·평균≤0도 aggregate 성과와 별개로 차단한다. 이 단계도 자동 승격하지 않는다. IEX-only 결과는 Challenger까지만 허용하고 SIP 또는 동등 consolidated feed 검증 뒤 시장 전체 Champion으로 승격한다.

## 문서

- [승인된 전체 설계](docs/superpowers/specs/2026-07-14-autonomous-paper-trading-research-os-design.md)
- [Lane control-plane 계약 설계](docs/superpowers/specs/2026-07-15-lane-control-plane-contracts-design.md)
- [Alpaca paper-only 안전 기반 구현 계획](docs/superpowers/plans/2026-07-14-alpaca-paper-foundation.md)
- [현재 코드 아키텍처](docs/architecture_ko.md)
- [KIS 실시간 연결 현황](docs/kis_live_integration_ko.md)
- [런타임 인과성·안전 감사](docs/runtime_audit.md)
- [Account Activities 체결 복구 체크포인트](docs/checkpoints/2026-07-15-paper-account-activities-ko.md)
- [부분체결 보호 OCO 계획 체크포인트](docs/checkpoints/2026-07-15-paper-protective-oco-plan-ko.md)
- [보호 OCO 원장·nested 복구 체크포인트](docs/checkpoints/2026-07-15-paper-protective-oco-ledger-ko.md)
- [보호 OCO armed smoke CLI 체크포인트](docs/checkpoints/2026-07-15-paper-protective-oco-smoke-cli-ko.md)
- [보호 OCO staged cancel·replacement 체크포인트](docs/checkpoints/2026-07-15-paper-protective-oco-resize-ko.md)
- [Lane control-plane 계약 체크포인트](docs/checkpoints/2026-07-15-lane-control-plane-contracts-ko.md)
- [cancel·EOD 평탄화 armed smoke CLI 체크포인트](docs/checkpoints/2026-07-15-paper-safety-mutation-smoke-cli-ko.md)
- [cancel·EOD 평탄화 smoke 구현 계획](docs/superpowers/plans/2026-07-15-paper-safety-mutation-smoke.md)
- [새 작업 시작 안내](CODEX_START_HERE.md)

## 현재 가능한 일

- KIS 실전 시세 서버의 NASDAQ·NYSE·AMEX 상승률·거래량 랭킹 조회
- KIS 읽기 전용 GET의 일시적 500/502/503/504를 80ms 뒤 정확히 한 번 재시도하고 반복 오류·429는 fail-closed 처리
- 각 scan cycle의 재시도·복구·최종 실패 수와 안전한 endpoint/종목 상세를 별도 CSV로 감사하고 일일 연구 원장 checksum에 포함
- 매 cycle의 상승률·거래량 원시 랭킹 행과 실제 선택 여부를 CSV에 누적
- 선택 후보의 완료 정규장 1분봉을 최초 관찰 시각과 함께 SQLite에 중복 없이 보존
- 실제 신호 평가에 사용한 전일 종가·평균 거래량·spread·최신 완료 봉을 후보 입력 snapshot으로 별도 보존
- 적격 장마감 세션의 시점 고정 후보 입력·최초 관찰 분봉만으로 VWAP/HOD/Gap-and-Go를 독립 재생하고, 불완전 종목은 0수익이 아닌 censored로 분리
- 한 번 선택된 후보를 해당 뉴욕 거래일 동안 watchlist에 유지하고 랭킹 탈락 뒤에도 추적
- 최초 선택 후보의 다음 1분봉 시가부터 EOD·MFE·MAE와 임계값·비용 민감도 진단
- ORB 81개 인접 파라미터의 실제 관찰시각·다음 1분봉 조건부 진입·최대 10포지션 전진성과 진단
- 첫 HOD 뒤 2~8봉 base와 1.5배 거래량 재확대를 요구하는 독립 HOD breakout paper 신호
- 첫 5분 half-gap 유지·시가·VWAP 상회로 continuation과 gap failure를 분리하는 Gap-and-Go paper 신호
- NYSE 공식 현재 거래정지·호가 없음·역전 호가·100bp spread·왕복비용 140bp 위험 게이트
- 전체 위험판정 후보를 보존하고 spread·slippage·왕복비용 27개 인접값마다 최대 10개를 재선정하는 진단
- 전체 위험판정 후보의 누적 volume·ADV를 저장하고 등락률·가격·거래대금·volume/ADV 81개 조합마다 최대 10개를 재선정하는 후보 진단
- 정규장에만 위험통과 전체 후보의 종목별 현재가상세를 조회해 전일 종가·당일 시가·시가 갭을 append-only 저장
- 급등 후보의 1분봉과 최근 일봉 문맥 수집
- 뉴욕 정규장, 당일 데이터, 3분 이내 최신 완료 봉 검증
- 5분 ORB·상대거래량·스프레드·위험폭 필터
- 조건부 진입가·손절가·1R·2R 목표가 생성
- 추천·무효·손절·목표 상태를 SQLite에 보존
- 종목별 마지막 완료 봉 checkpoint와 재시작 중복 방지
- 날짜별 영속 감시, 부분 실패 cycle 감사, 정규장 종료 시 자동 중단
- NYSE 공식 2026~2028 휴장일·13:00 조기폐장 반영, 지원범위 밖 fail-closed
- SQLite immutable outbox와 JSONL·한국어 추천 카드 projection
- KIS 알림은 스캔 직전 5분 이내 생성된 추천만 queue해 과거 추천의 지연 발송 차단
- 정규장 종료 시 마지막 완료 봉 가격으로 열린 paper 추천을 당일 `time_exit`
- 정규장 종료 65초 뒤 tracked 후보의 마지막 15:59 봉을 한 페이지씩 순차 보강하고 기존 추천만 갱신한 뒤 metrics 실행
- 완료된 paper 거래를 편도 5/10/20bp 비용으로 집계하고 연도별 결과·bootstrap CI·장 마감 fallback 비율을 출력
- checksum으로 고정한 개별 거래 원장에서 5/10/20/60 적격일 롤링 성과와 장전 시장 국면별 성과를 계산하고 조기중단·진단·비교·최종검토 권고를 출력
- CSV 분봉 replay
- Alpaca Paper 고정 도메인·별도 mode 600 자격증명·무리다이렉트 GET client
- 계좌 식별자를 저장하지 않는 SHA-256 fingerprint 기반 실행 원장 결합
- 단일 Writer 파일 잠금, WAL, 외래키, UPDATE/DELETE 금지 trigger가 있는 append-only 원장
- 세 lane의 immutable manifest·분리 account/ledger binding·사전등록 experiment scope·final daily snapshot을 저장하는 별도 append-only registry와 query-only Reviewer reader
- `DailyResearchRecord` schema v2와 adaptive evaluator의 exact scope key 표본 격리, schema v1 원장·개별 record 무재작성 intraday projection
- 계좌·주문·포지션과 로컬 intent를 전체 필드로 비교하는 fail-closed preflight
- Alpaca Paper 시장시계 GET과 고정 WSS endpoint의 `trade_updates` 인증·구독·Ping/Pong
- text/binary frame 원문 BLOB을 먼저 append-only 저장하고 검증 결과를 별도 disposition으로 남기는 raw-first 체결 원장
- 재시작 시 미분류 raw receipt를 원래 수신 순서·연결 세대·수신시각으로 다시 분류하고 불일치·미지 주문을 격리하는 복구 경로
- heartbeat 사이 계좌·open 주문·미해결 intent 주문·최근 7일 주문·포지션을 안전하게 페이지 조회해 원장과 대사하는 GET-only REST recovery
- Account Activities FILL을 ID cursor로 전 페이지 순회하고 엄격 파싱한 뒤 SQLite v4 append-only 원장에 복구 브래킷별로 보존
- REST 누적 체결 snapshot과 WSS·Account Activities 개별 execution 증거를 분리해, 세 출처가 정확히 일치할 때만 누락된 WSS 체결 상세를 복구하는 projection
- 검증된 부분체결과 현재 포지션이 일치할 때 정확한 수량의 DAY OCO(stop-market + 2R limit)를 결정론적으로 계획하고, broker 보호 OCO 확인 전에는 모든 신규 진입을 차단
- schema v5 불변 보호 OCO 계획·두 leg recovery 원장, `nested=true` open/recent 주문 분리, 계획·수량·가격·현재 heartbeat 일치 대사
- schema v9 `cancel_protective_oco` mutation 원장, 기존 계획·관측된 parent broker order source binding, timeout exact-ID 복구와 호출 간 staged cancel·replacement
- 스트림의 두 Pong 사이 REST·원장 재대사와 공개 의존성 주입 없는 활성 세션 전용 정규장·최신 완료 1분봉·전체 포트폴리오 위험 승인 상태기계
- 체결된 parent intent를 명시해 current-epoch 보호 OCO를 제출하는 arm 필수 smoke CLI. 차단·noop·ack 보고서는 남기지만 정규장 안전조건이 부족하면 실제 POST를 실행하지 않는다
- current-epoch 안전계획의 entry·보호 OCO 취소와 exact 정수 포지션 평탄화를 순서대로 실행하는 arm 필수 smoke CLI. 축소 한도는 notional 100 USD·계획위험 10 USD·1포지션·일손실 30 USD·편도 20bp로 고정되며, mutation broker를 열기 전에 계획을 만든 동일 REST snapshot의 주문·포지션·symbol 수와 합산 notional에 실제 적용된다

## 실행

로컬 lane registry는 네트워크나 자격증명 없이 초기화할 수 있다. 기존 intraday execution 원장을 지정하면 이미 저장된 account fingerprint와 binding 시각만 읽어 전용 lane 결합을 등록하며, 보고서에는 fingerprint·경로·registry key를 쓰지 않는다.

```bash
./run_lane_control_plane_bootstrap.py \
  --database outputs/lane_control/lane_registry.sqlite3 \
  --output-dir outputs/lane_control/latest \
  --intraday-execution-database outputs/paper_execution/paper_execution.sqlite3
```

Alpaca Paper 안전 기반은 별도 `~/.config/trading-agent/alpaca-paper.env`의 paper 계정 키만 사용한다. 파일 권한은 정확히 `600`이어야 한다.

```text
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
```

최초 한 번 실행 원장과 현재 빈 Paper 계정을 결합한다. 이 명령의 외부 호출은 계좌·미체결 주문·포지션 GET뿐이며 broker 상태를 변경하지 않는다.

```bash
./run_alpaca_paper_bootstrap.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/bootstrap/latest
```

이후 시작 전 안전 대사를 수행한다. preflight는 로컬 실행 원장을 생성하거나 수정하지 않으며, 미결합·계좌 변경·알 수 없는 주문·주문 필드 불일치·미해결 intent·열린 포지션을 발견하면 준비 상태를 거부한다.

```bash
./run_alpaca_paper_preflight.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/preflight/latest
```

`bootstrap`과 `preflight` 모두 실제 주문을 제출하지 않는다.

주문 스트림과 REST를 한 연결 세대 안에서 실제 확인하려면 다음 명령을 실행한다. 스트림 Ping → 계좌·미체결·포지션·시장시계 GET → 단일 SQLite 원장 스냅샷과 대사·포트폴리오 집계 → 스트림 Ping 순서로 검사한다. 장이 닫혀 있어도 이 읽기 전용 probe가 정상이면 성공하지만, 결과는 세션 종료 뒤 주문 승인에 재사용할 수 없고 보고서도 현재 주문 승인을 주장하지 않는다.

```bash
./run_alpaca_paper_readiness.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/readiness/latest
```

이 명령도 POST/DELETE를 호출하지 않는다. 실제 주문 승인에는 열린 런타임 세션 안에서 5초 이내 두 Pong과 같은 `connection_epoch`의 REST·원장 대사를 먼저 통과해야 한다. 그 뒤 브로커와 로컬 정규장 일치, 폐장 30분 전 이전, 방금 완성된 정확한 현재 정규장 1분봉, 현재 스트림 상태, 부분체결 포함 전체 포트폴리오 위험을 순서대로 검사한다. 기존 노출은 원장 손절거리와 왕복 최소 20bp 비용으로 다시 계산해 종목별 위험·명목 한도를 적용하고, 신규 수량은 여기에 관측 spread까지 포함해 내부 산정한다.

현재 계좌의 cutoff·kill switch·EOD 조치를 current-epoch에서 계산하고 로컬 불변 원장에만 저장하려면 다음 명령을 사용한다. 15:30 ET부터 남은 entry를 취소 대상으로, 일손실 USD 300 도달 시 또는 15:55 ET부터 entry·보호 OCO 취소 뒤 정수 주식 포지션 평탄화를 계획한다. 실제 broker 주문 변경은 아직 수행하지 않는다.

```bash
./run_alpaca_paper_safety.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/safety/latest
```

계획은 계좌 fingerprint·뉴욕 거래일·MTM 손익·미청산 계획위험을 차감한 보수적 손익·순서화된 조치를 schema v9 append-only 원장에 결합한다. 당일 kill 기록은 재시작이나 equity 회복 뒤에도 신규 진입을 다시 열지 않으며 다음 뉴욕 거래일에만 만료된다. 이 계획 CLI 자체의 외부 동작은 WSS와 REST GET뿐이다.

Paper mutation 요청이 timeout 또는 응답 형식 오류로 모호해졌을 때는 다음 GET-only 명령으로만 복구한다. 같은 단일 Writer/WSS의 current `connection_epoch`에서 open·targeted·최근 주문, nested OCO와 포지션을 다시 대사한다. 정확한 broker 주문이 확인되면 `RECOVERED_ACKNOWLEDGED`, 안전한 정착시간 뒤 요청 부재가 증명되면 `RECOVERED_ABSENT`를 schema v9 원장에 append한다. 그 전에는 동일 POST/DELETE를 다시 보내지 않는다.

```bash
./run_alpaca_paper_mutation_recovery.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/mutation_recovery/latest
```

이 명령 자체는 WSS와 REST GET만 사용한다. 신규 진입 production 경계는 방금 완성된 현재 1분봉 후보와 current-epoch 승인 결과만 받아 DAY limit 주문으로 고정하며, 다음처럼 정확한 arm 값과 모든 시점 입력을 명시해야 한다. 위험 한도는 notional 100 USD·계획위험 10 USD·포지션 1개·일손실 30 USD로 코드에 고정된다.

```bash
./run_alpaca_paper_entry_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/entry_smoke/latest \
  --intent-id orb-AAPL-YYYYMMDD-HHMMSS --symbol AAPL \
  --entry-limit 10.00 --stop 9.75 --target-1r 10.25 --target-2r 10.50 \
  --created-at 2026-07-14T09:36:02-04:00 \
  --bar-start 2026-07-14T09:35:00-04:00 \
  --bar-first-observed 2026-07-14T09:36:01-04:00 \
  --liquidity-quantity 100 --spread-bps 20
```

정규장·현재 봉·빈 포트폴리오·WSS heartbeat·계좌 대사 중 하나라도 틀리면 POST 전에 차단한다. 이 경계는 MockTransport와 fake CLI로 검증됐지만 실제 정규장 최소 주문은 아직 보내지 않았다.

진입 체결 뒤 보호 OCO를 별도 smoke하려면 정확한 parent intent를 지정한다. 이 명령도 같은 arm 값과 단일 Writer/WSS current-epoch 복구를 요구하며, 체결 원장·broker 포지션·보호 OCO 계획이 일치하지 않으면 POST 전에 차단한다.

```bash
./run_alpaca_paper_protective_oco_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/protective_oco_smoke/latest \
  --intent-id orb-AAPL-YYYYMMDD-HHMMSS
```

이 CLI는 이미 체결된 축소 Paper entry의 보호 주문 수명주기를 검증하기 위한 것이며, 신규 진입을 만들지 않는다. 현재 5초 REST/WSS·ACTIVE 계좌 대사·브로커/로컬 정규장 일치·15:55 ET 이전 조건이 모두 맞아야 mutation broker를 연다. 기존 OCO가 exact 포지션을 덮으면 noop이다. 추가 체결로 수량이 부족하면 첫 호출은 source-bound OCO cancel만 실행해 `incomplete`/종료코드 2로 끝나며, broker terminal 상태를 다음 current-epoch에서 대사한 뒤 다시 실행할 때만 고유 client ID의 exact-quantity replacement OCO를 POST한다. cancel과 replacement를 한 호출에서 함께 전송하지 않는다.

cutoff·kill switch·EOD 계획을 실제 Paper cancel/flatten mutation으로 별도 smoke하려면 다음 명령을 사용한다. 현재 WSS 세대 안에서 미해결 mutation을 먼저 복구하고 broker·원장·계좌 fingerprint가 맞을 때만 계획 순서대로 entry와 보호 OCO를 취소한 뒤 exact 정수 포지션을 평탄화한다.

```bash
./run_alpaca_paper_safety_mutation_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/safety_mutation_smoke/latest
```

이 CLI는 Paper-only DELETE를 실행할 수 있다. mutation 전에는 entry order·position·보호 OCO가 각각 최대 1개이고 전체 mutation 대상이 한 symbol이며, 현재 포지션 market value와 미체결 entry 잔량의 limit notional 합이 100 USD 이하인지 확인한다. 하나라도 넘거나 notional을 확정할 수 없으면 broker adapter를 열지 않는다. cancel과 close가 한 계획에 있으면 첫 호출은 cancel만 원장에 확정하고 current-epoch 대사 뒤 종료한다. 이때 close가 아직 미실행이므로 결과는 `incomplete`, 종료코드는 2다. 같은 명령을 다시 실행해 broker 주문이 terminal이고 새 exact 포지션 수량으로 close-only 계획이 만들어졌을 때만 평탄화를 제출한다. 모든 현재 계획 조치가 `acknowledged` 또는 `already_acknowledged`일 때만 종료코드 0이며, mutation 전 current-epoch 차단은 1, 거절·모호·일부 미실행 또는 mutation 뒤 대사 실패는 2다. 오류·차단 보고서는 원시 broker/recovery 사유를 출력하지 않는다. 정규장 smoke는 축소 entry 체결과 보호 OCO 대사 직후 또는 EOD 경계에서만 수행하고, 종료 뒤 open order 0·position 0·broker/shadow/원장 일치를 별도 GET 대사로 확인해야 한다.

재시작 직후에는 다음 명령으로 미분류 raw receipt를 먼저 처리하고, 같은 WSS 연결 세대의 두 heartbeat 사이에서 REST 주문 snapshot을 원장에 저장한다.

```bash
./run_alpaca_paper_recovery.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/recovery/latest
```

복구 명령은 WSS 인증·구독과 REST GET만 사용한다. open/recent 주문은 `nested=true`로 읽어 entry와 보호 OCO를 분리하고, OCO는 사전 저장된 계획이 정확히 하나일 때만 두 leg를 append-only 저장한다. 정상 종료는 복구 snapshot 저장과 현재 로컬 차단 사유 부재만 뜻하며, 종료된 세션의 결과로 새 주문 admission을 승인하지 않는다. REST 누적 체결량만 있고 개별 execution이 없으면 aggregate 상태는 복원하되 체결 상세를 완전하다고 만들지 않는다. Account Activities FILL의 개별 수량·누적 수량·잔량·평균가격이 REST 주문과 정확히 일치할 때만 WSS 누락 상세를 보강한다. 같은 activity ID의 payload 변경, activity 누락으로 인한 REST 누적값 감소, 새 모순 activity와 출처 간 불일치는 자동 수정하지 않고 차단한다. Alpaca WSS에는 replay cursor나 처리 high-water 보장이 없고 공식 FILL activity 형식에도 별도 correction/bust 이벤트가 명시돼 있지 않으므로 이 명령 하나가 이벤트 무손실이나 체결 정정 복구를 증명하지 않는다.

Alpaca 과거 SIP 1분봉 아카이브:

```bash
./run_alpaca_minute_archive.py \
  --start 2026-06-12 --end 2026-06-12 \
  --symbols-file examples/alpaca_probe_symbols.txt
```

`--symbols-file`을 생략하면 Alpaca Assets API의 현재 `active+inactive` 미국 상장 종목을 조회하고 유니버스 스냅샷을 저장한다. OTC는 제외한다. 데이터는 날짜·유니버스 지문·종목 묶음별 `CSV.gz`와 메타데이터로 즉시 확정되며, 재실행은 동일 날짜·동일 유니버스·동일 종목 묶음의 완료 체크포인트를 재사용한다. 장전 04:00부터 장후 20:00 ET까지 `feed=sip`, `1Min`, `adjustment=raw`, 날짜별 `asof`로 요청한다. 무료 한도에 맞춰 요청 시작 간격을 0.31초 이상으로 유지하고 RSS가 10GiB에 도달하면 중단한다.

자격증명은 `~/.config/trading-agent/alpaca.env`에 다음 이름으로 저장하고 권한을 `600`으로 설정해야 한다.

```text
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
```

Alpaca의 현재 Assets API는 비활성 종목을 포함하지만 완전한 과거 point-in-time 종목마스터는 아니다. 따라서 상장폐지·티커 변경·기업행위 편향은 별도 품질 진단 대상으로 유지한다.

KIS paper 스캔:

```bash
./run_kis_paper_scan.py --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy vwap_reclaim --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy hod_breakout --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy gap_and_go --top 3 --max-pages 10
./run_kis_daytime_scan.py --top 10
./run_session_continuity.py outputs/live_sessions/<거래일>
```

`orb`, `vwap_reclaim`, `hod_breakout`, `gap_and_go`는 별도 전략 이름과 출력 폴더로 실행한다. 성과를 합쳐 유리하게 만들지 않으며, 기본값은 `orb`다.

KIS 날짜별 paper 감시:

```bash
./run_kis_paper_watch.py --wait-until-open --max-wait-minutes 720 \
  --collect-premarket --premarket-interval-seconds 300 \
  --cycles 390 --interval-seconds 60 --top 10 --max-pages 1
```

장 전에 실행하면 최대 대기시간 안에서 뉴욕 정규장 개장을 30초 간격으로 확인한 뒤 시작한다. `--wait-until-open`을 생략하면 폐장 중에는 API를 호출하지 않고 즉시 종료한다.
`--collect-premarket`을 사용하면 04:00~09:29 ET에는 전용 읽기 전용 CLI가 원시 랭킹과 위험판정만 기본 5분 간격으로 저장하고, 09:30부터 기존 전략 감시로 전환한다. 장전에는 추천 DB·후보 watchlist·분봉 전략 평가를 만들지 않는다.
`--top`은 위험 통과 뒤 스냅샷별 포트폴리오 상한이며 기본값은 10이다. 조건을 충족하지 않는 종목을 강제로 채우지 않는다.
`--max-pages`는 반복 cycle의 종목별 분봉 페이지 상한이며 기본값은 1이다. 최근 120개 봉을 다시 읽고 앞선 완료 봉은 SQLite에 누적해 매분 과거 10페이지를 반복 요청하지 않는다.

하루 감시가 폐장 3분 이내에 끝나면 16:01:05 ET까지 기다린 뒤 별도 `run_kis_eod_catchup.py`를 한 번만 순차 실행한다. 이 child는 당일 `tracked_candidates`만 읽고 종목마다 최신 한 페이지에서 15:59 봉을 확인해 보존한다. 신규 신호나 후보 입력 snapshot은 만들지 않고 이미 열린 추천만 갱신한다. 마지막 봉이 없거나 종목 조회가 실패하면 child 종료코드가 1이며, 그 뒤 fallback 보고와 metrics는 남기되 완전한 challenger 경로로 간주하지 않는다. 짧은 수동 watch가 폐장보다 3분 이상 일찍 끝나면 장시간 대기하지 않고 EOD 단계를 건너뛴다.

EOD child의 종목별 결과와 요약은 `kis_eod_catchup_observations.csv`, `kis_eod_catchup_summary.csv`에 남고, child 종료상태는 `eod_catchup_cycles.csv`, 읽기 재시도는 정규장 cycle과 섞지 않은 `eod_kis_read_retry_*`에 저장된다.

신규 추천이 있으면 같은 출력 폴더에 다음 파일이 생성된다.

- `recommendation_alerts.jsonl`: 외부 알림 어댑터용 구조화 카드
- `recommendation_alerts_ko.md`: 사람이 확인하는 한국어 카드
- `paper_recommendations.sqlite3`: 중복 방지의 원본 immutable outbox

추천 유무와 관계없이 `kis_ranking_snapshots.csv`에는 관찰 시각, 랭킹 출처, 거래소, 원천 순위, 가격·등락률·호가·거래량·거래대금과 실제 선택 여부가 append-only로 저장된다. `kis_ranking_request_coverage.csv`에는 거래소×상승률/거래량 요청 6개의 성공 여부와 행 수·실패 사유를 cycle마다 남긴다. 한 요청이 실패해도 성공한 거래소 후보는 계속 shadow 평가하지만, 보고서는 `부분 모집단`으로 표시되고 child 종료코드는 1을 유지한다. `market_risk_screen.csv`에는 공식 현재 거래정지, 호가 결손·역전, spread, 편도 20bp 슬리피지 예비비를 합친 예상 왕복비용과 최종 선정 여부를 별도로 누적한다.

장전 수집을 사용하면 같은 구조의 `premarket_ranking_snapshots.csv`와 `premarket_risk_screen.csv`, child 종료 상태를 담은 `premarket_watch_cycles.csv`가 추가된다. KIS 누적 거래량은 실제 세션 reset을 검증하기 전에는 장전 전용 RVOL로 해석하지 않는다.

`run_kis_daytime_scan.py`는 KIS 미국 주간거래 시간에만 `BAQ/BAY/BAA` 랭킹을 읽는다. `daytime_ranking_snapshots.csv`, `daytime_risk_screen.csv`, `daytime_session_map.csv`를 별도로 저장하고 이 가격을 정규장 시가·opening gap으로 사용하지 않는다.

`run_session_continuity.py`는 세션별 전체 위험판정 후보를 비교해 주간거래→프리마켓→정규장 재등장률을 별도 CSV와 한국어 보고서로 만든다. 미래 세션이 아직 없으면 연속률을 공란으로 두며 수익성 결과로 해석하지 않는다.

`kis_opening_gap_cycles.csv`는 정규장 여부와 시가 조회 성공·실패 수를 남긴다. `kis_opening_gap_snapshots.csv`는 정규장 중에만 포트폴리오 한도 밖을 포함한 위험통과 후보의 전일 종가·당일 시가·갭·현재/전일 거래량을 저장한다. 폐장에는 이전 거래일 시가를 현재 시가로 소급하지 않는다. 이 값은 아직 추천 선정에 사용하지 않으며 forward 표본이 쌓인 뒤 인접값을 별도로 승격한다.

같은 SQLite의 `candidate_minute_bars`에는 선택 후보의 완료 정규장 OHLCV·거래대금과 최초 관찰 시각을 저장한다. 동일 거래소·종목·분봉을 다시 조회해도 최초 행을 유지해 당시 알려진 데이터 경로를 재현한다.

`candidate_input_snapshots`에는 신규 신호를 평가한 후보의 실제 관찰 시각, 당시 최신 완료 봉, 완료 일봉에서 계산한 전일 종가·20일 평균 거래량과 관측 spread를 append-only로 저장한다. 사후 challenger replay는 이 입력과 `candidate_minute_bars`를 함께 사용해야 하며, 현재 랭킹이나 나중에 수정된 일봉 문맥으로 대체하면 안 된다.

`candidate_input_cycles.csv`는 watch cycle별 선정 후보 수, 실제 입력 snapshot 수와 scan 완료 여부를 남긴다. 일일 연구 원장은 이 행 수가 `watch_cycles.csv`와 같고, 모든 scan이 완료됐으며, 보고된 snapshot 합계가 SQLite 실제 행 수와 일치해야 해당 날짜를 적격으로 판정한다.

장마감 challenger causal replay:

```bash
./run_shadow_challenger_replay.py outputs/live_sessions/<거래일> \
  --strategy gap_and_go \
  --output-dir outputs/challengers/<거래일>/gap_and_go
```

`vwap_reclaim`, `hod_breakout`, `gap_and_go`만 challenger로 허용한다. source 날짜가 일일 품질 게이트를 통과하고 장마감 metrics 성공 감사행이 있어야 하며, 후보 입력이 미완료 봉을 가리키거나 정규장 1분 경로가 완전하지 않으면 각각 날짜 거부 또는 종목 검열로 처리한다. 전략마다 별도 SQLite·추천 보고서·5/10/20bp metrics·종목 커버리지·게이트를 생성한다. 아직 ORB와 동일한 최대 포지션·위험 예산으로 재선정하는 비교기는 연결하지 않았으므로 출력의 `comparison_eligible`은 `false`이며 champion 승격에 사용하지 않는다.

정규장에서 한 번 선택된 종목은 `tracked_candidates`에 거래일 단위로 저장한다. 이후 상위 랭킹에서 빠지면 새 추천은 만들지 않고 분봉 보존과 이미 열린 추천의 손절·목표 상태 갱신만 계속한다. 폐장·휴장에는 watchlist를 만들거나 이전 거래일 후보를 불러오지 않는다.

CSV replay:

```bash
./run_trading_agent_replay.py examples/example_intraday.csv \
  --output-dir outputs/replay/example
```

누적 paper 성과 보고서:

```bash
./run_paper_metrics.py outputs --output-dir outputs/paper_metrics/latest
```

`active` 뒤 손절·2R·당일 종료가 확인된 추천만 거래로 집계한다. 출력은 `paper_metrics.csv`, `paper_yearly_metrics.csv`, `paper_trades.csv`, `paper_metrics_ko.md`이며, 현재 저장된 2건은 기능 검증용 QA 표본이므로 수익성 증거가 아니다. 날짜별 watch가 공식 정규장 종료 뒤 끝나면 같은 CLI를 자동 실행해 세션의 `paper_metrics/`에 저장하고 `post_session_metrics_cycles.csv`에 종료코드를 남긴다. 장중 단발 watch나 DB가 없는 실행은 자동 일일 평가를 만들지 않는다.

장마감 Paper 연구 원장:

```bash
./run_daily_research_record.py outputs/live_sessions/<거래일> \
  --session-date YYYY-MM-DD --strategy orb
```

장마감 metrics가 성공하면 watch가 이 CLI도 순차 실행한다. 세션별 불변 JSON, 상위 `daily_research_ledger.jsonl`, 한국어 요약과 별도 종료코드를 남기며 코드·데이터·평가기·파라미터·비용·품질 계보를 함께 고정한다. 랭킹 6개 요청과 watch cycle이 완전하지 않거나 실패 cycle이 있으면 해당 날짜를 적격 forward day로 세지 않는다. 평균수익 CI는 뉴욕 거래일 전체를 블록으로 재표본화하며 원장 평가기 버전은 `paper_metrics_day_block_bootstrap_v2`다. 최소 60거래일·100건 외에도 broker ledger, DSR/PBO, 인접 파라미터 평탄성, SIP 검증이 없으므로 자동 승격과 자동 주문은 계속 금지한다.

적응형 전략 평가:

```bash
./run_adaptive_strategy_evaluation.py outputs/live_sessions/<거래일>
```

일일 연구 원장이 성공하면 watch가 이 CLI를 마지막으로 순차 실행하고 `post_session_adaptive_evaluation_cycles.csv`에 종료코드를 남긴다. `paper_metrics/paper_trades.csv`가 원장 checksum과 다르거나 연구 record를 세션 폴더 하나로 결정할 수 없으면 fail-closed한다. 시장 국면은 선택 artifact인 `research_regime_snapshot.json`이 해당 거래일 정규장 개장 이전에 관측되고 원장 checksum에 포함됐을 때만 분할 평가에 사용한다. 라벨이 없으면 `unclassified`로 추정하지 않고 최종 검토의 coverage·다양성 문턱을 통과시키지 않는다.

종목 차이는 반복 가능성이 낮은 ticker 이름이 아니라 추천 생성시각에 실제로 알려진 가격·opening gap·누적 volume/ADV·거래대금 cohort로 분리한다. `candidate_input_snapshots`의 정확한 추천 생성시각과 거래소를 먼저 고정한 뒤, 그 시각 이하의 최신 checksum된 `market_risk_screen.csv`와 `kis_opening_gap_snapshots.csv`만 조인한다. 미래 행은 무시하며 원천이 없으면 `censored`다. 사전 구간은 가격 `<$5/$5~20/$20~50/$50+`, gap `<4%/4~10%/10~20%/20%+`, volume/ADV `<10%/10~25%/25~50%/50%+`, 거래대금 `<$1M/$1~5M/$5~20M/$20M+`다. 출력은 `adaptive_evaluation.json`, `adaptive_evaluation_ko.md`, 개별 조인 감사용 `trade_feature_assignments.csv`이며 전략 상태나 주문 권한을 자동 변경하지 않는다.

스캐너 forward outcome 진단:

```bash
./run_scanner_forward_metrics.py outputs/live_sessions/<거래일> \
  --output-dir outputs/scanner_forward_metrics/<거래일>
```

종목·거래일 최초 실제 선택 1건만 사용하고, 다음 완전한 1분봉 시가부터 공식 close 직전 봉까지 연속 경로가 있을 때만 5/15/30분·EOD·MFE·MAE를 계산한다. 중도절단 경로는 별도 표시하고 성과 0이나 완료 거래로 바꾸지 않는다.

ORB forward paper 진단:

```bash
./run_orb_forward_metrics.py outputs/live_sessions/<거래일> \
  --output-dir outputs/orb_forward_metrics/<거래일>
```

OR 1/5/15분, 거래량 1.0/1.5/2.0배, 손절폭 0.75/1.0/1.25배, 목표 1R/2R/3R의 81개 조합을 비교한다. 각 조합은 편도 5/10/20bp, PF·승률·평균·누적·MDD·bootstrap CI와 연도별 결과를 출력한다. 랭킹 응답 뒤 실제 분봉 조회가 끝난 시각 이후만 신호로 인정하고, 다음 완전한 1분봉부터 조건부 진입하며 동시 보유는 상승률·거래대금 순 최대 10개다.

시장위험 인접값 진단:

```bash
./run_market_risk_sensitivity.py outputs/live_sessions/<스캔폴더>
```

spread 80/100/120bp, 편도 slippage 10/20/30bp, 최대 왕복비용 100/140/180bp의 27개 조합을 비교한다. 각 조합은 저장된 전체 위험판정 후보에서 다시 필터링하고 최대 10개를 재선정한다. 출력은 후보 보존율과 선택 목록이며 수익성 백테스트가 아니다.

스캐너 후보 인접값 진단:

```bash
./run_scanner_candidate_sensitivity.py outputs/live_sessions/<스캔폴더>
```

등락률 4/6/8%, 최대가격 20/50/200달러, 누적 거래대금 0.5/1/2백만 달러, 시점 누적 거래량/ADV 0.05/0.10/0.20의 81개 조합을 전체 위험판정 후보에서 다시 선정한다. 후행수익·PF 결과가 아니며 KIS 랭킹에 없는 전체 후보 opening gap은 계산하지 않는다.

테스트:

```bash
uv run pytest -q
```

## 폴더 구조

```text
trading-recommendation-agent/
├── trading_agent/        추천·전략·리스크·공급자·Paper 실행 기반
├── scr_backtest/         KIS 분봉 저수준 어댑터
├── tests/                인증·최신성·인과성·추천 회귀 테스트
├── docs/                 설계·실데이터 연결·런타임 감사
├── examples/             공급자 독립 분봉 예시
├── artifacts/            검증된 실행 결과 표본
├── outputs/              새 실행 결과, Git 제외
├── run_kis_paper_scan.py
├── run_kis_paper_watch.py
├── run_alpaca_minute_archive.py
├── run_alpaca_paper_bootstrap.py
├── run_alpaca_paper_entry_smoke.py
├── run_alpaca_paper_preflight.py
├── run_alpaca_paper_readiness.py
├── run_alpaca_paper_recovery.py
├── run_kis_daytime_scan.py
├── run_session_continuity.py
├── run_paper_metrics.py
├── run_daily_research_record.py
├── run_adaptive_strategy_evaluation.py
├── run_orb_forward_metrics.py
├── run_market_risk_sensitivity.py
├── run_scanner_candidate_sensitivity.py
├── run_scanner_forward_metrics.py
└── run_trading_agent_replay.py
```

## 보안

자격증명은 프로젝트 안에 저장하지 않는다. KIS와 Alpaca market data는 각각 `~/.config/trading-agent/kis.env`, `~/.config/trading-agent/alpaca.env`를 사용한다. Paper 계좌 조회와 향후 실행은 별도 `~/.config/trading-agent/alpaca-paper.env`만 사용한다. Paper 파일은 일반 파일·현재 사용자 소유·정확한 mode `600`을 모두 요구한다.

Alpaca paper 코드는 trading base URL을 설정값으로 자유롭게 받지 않는다. REST는 정확한 `https://paper-api.alpaca.markets`, 주문 스트림은 정확한 `wss://paper-api.alpaca.markets/stream`만 허용하고 다른 URL은 자격증명 전송 전에 거절한다. REST 리다이렉트도 따르지 않는다. KIS는 계속 시세 조회 전용이다.

원본 Notion 페이지에 평문으로 남아 있는 기존 앱 키·시크릿은 운영 전 재발급하고 삭제해야 한다.

## 현재 한계

- KIS 랭킹 상위 후보를 감시하며 미국 전체 종목 원시 스트림을 완전히 열거하지 않는다.
- 영속 감시는 NYSE가 게시한 2026~2028 캘린더를 반영한다. 2029년 이후 일정과 임시 휴장 변경은 표를 갱신하기 전까지 안전하게 닫힌다.
- 로컬 추천 카드 outbox는 구현했지만 Telegram·Codex 외부 전송 어댑터는 아직 연결하지 않았다.
- 장 마감 `time_exit` 가격은 실제 MOC가 아니라 마지막 처리 완료 봉 fallback이므로 성과 집계에서 별도 구분해야 한다.
- 성과 대시보드는 구현됐지만 실제 정규장 paper 거래가 아직 누적되지 않아 수익성은 검증되지 않았다.
- 현재 paper 추천 전략은 ORB, 첫 눌림목 VWAP reclaim, 첫 HOD 거래량 돌파, Gap-and-Go 지속이다. 모두 구현·인과성 회귀만 완료됐고 실제 정규장 성과는 아직 0건이다.
- lane manifest·binding·experiment scope·daily snapshot registry는 구현됐지만 독립 Reviewer의 승격 event projection과 Portfolio Manager는 아직 없다. Portfolio Manager는 최소 두 lane champion 전에는 구현하거나 주문권한을 갖지 않는다.
- Alpaca Paper GET 대사, 주문 스트림 control plane·heartbeat, raw-first `trade_updates` 영속화·격리·재시작 복구, Account Activities FILL 기반 누락 execution 보강, 부분체결 보호 OCO 계획·durable nested leg 대사, 단일 WSS/Writer generation barrier, cutoff·일손실 kill·EOD 평탄화 계획, schema v9 mutation attempt/outcome 원장과 exact entry/OCO/protective-cancel targeted current-epoch timeout 복구는 구현됐다. 추가 체결의 보호 OCO cancel·terminal 대사·다음 호출 replacement 상태기계도 fake broker에서 검증됐다. Paper-only entry POST, 보호 OCO POST, cancel/flatten DELETE는 명시적 arm 단발 CLI까지 열렸지만 실제 주문 실증은 아직 0건이다. 첫 정규장 smoke와 보호 OCO·최종 flat broker/shadow 대사 전에는 ORB 반복 pilot을 허용하지 않는다.
- PIT free float가 없으므로 low-float를 추정하지 않는다. 거래대금 기준은 저유동성 대리필터이며 float 필터가 아니다.
- ORB 신호 시각은 돌파 1분봉의 시작이 아니라 봉 완료 뒤로 기록한다. 15:59 봉처럼 확인 가능 시각이 정규장 close인 신호는 신규 추천에서 제외한다.

새 Codex 작업은 [CODEX_START_HERE.md](CODEX_START_HERE.md)를 먼저 읽고 이어서 진행한다.
