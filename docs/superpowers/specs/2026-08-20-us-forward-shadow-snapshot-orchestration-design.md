# 미국장 Forward Shadow 운영 스냅샷 연결 설계

**작성일:** 2026-08-20
**상태:** 사용자 설계 승인 완료, 명세 검토 대기
**대상:** 현재 XNYS 정규장 완료봉을 기존 US Forward Shadow tick으로 연결하는 read-only 운영 수직선

## 1. 결정 요약

미국장 Forward Shadow는 새 Alpaca 호출기를 만들지 않는다. 기존 수집기가 로컬에 남긴 세 종류의
point-in-time 증거를 읽어 하나의 `UsForwardShadowTick`을 만들고, 기존
`run_us_forward_shadow_tick.py` 런타임을 호출한다.

1. 완료봉과 sequence continuity: `MarketDataRuntimeStore`
2. 현재 bid/ask와 수신 계보: bounded `AlpacaSipDynamicReceiptStore`
3. 전일 종가와 평균 일거래량: 완료 상태의 Alpaca daily cache

새 one-shot orchestration CLI는 외부 스케줄러가 완료봉마다 호출한다. 정확히 같은 완료봉과 입력 계보는
같은 tick identity를 만들며, 재호출은 기존 trial/event/outcome을 중복시키지 않는다. 입력이 오래됐거나,
세션·symbol·instrument 또는 각 source 내부의 connection lineage가 다르거나, sequence gap 또는 불완전한 quote history가 있으면
tick을 만들지 않고 fail closed 한다.

이 수직선은 연구 전용이다. broker, account, balance, position, order, credential 또는 HTTP/WebSocket client를
import하거나 호출하지 않는다. 수익성 주장을 만들지 않고 Alpaca Paper 주문에도 연결하지 않는다.

## 2. 범위와 비범위

### 범위

- 현재 XNYS 정규 세션의 최신 완료 1분봉과 같은 세션 warm-up bars 읽기
- runtime checkpoint와 receipt sequence를 통한 reconnect/gap 검증
- bounded SIP raw receipt에서 현재 quote를 재구성하고 continuity·freshness 검증
- 완료된 daily cache에서 해당 symbol의 prior close와 average daily volume 읽기
- 위 증거를 `BarFrame`, `CandidateFrame`, `QuoteValidation`, `EvidenceRef`로 투영
- private immutable tick artifact 발행
- 기존 Forward Shadow controller의 one-tick 실행
- 재시작·동일 입력 replay의 결정론과 멱등성
- 스케줄러가 판독할 수 있는 구조화된 READY/REPLAYED/BLOCKED 결과

### 비범위

- Alpaca REST/WebSocket 연결 또는 credential 로딩
- Alpaca live 또는 Paper 주문, 취소, 계좌·포지션 변경
- KIS·LS 또는 다른 provider 호출
- 전체 universe scan, 과거 recommendation 생성 또는 backfill trial
- 가설 생성, capsule 승격, 다음날 정책 산출
- 한국장 Shadow 연결
- 기존 market-data collector와 dynamic SIP collector의 생명주기 통합

가설 생성과 정책 선택은 상위 Discovery/Policy loop의 책임이다. 이번 수직선은 이미 유효한 US
`ExplorationPolicy`와 선택된 research-only capsules를 실제 장중 증거에 연결하는 역할만 맡는다.

## 3. 채택한 접근과 기각한 대안

### 3.1 채택: 기존 append-only 로컬 증거 결합

기존 수집기가 이미 소유하는 append-only 저장소를 read-only로 읽는다. 수집 장애와 연구 실행 장애를 분리할 수
있고, 네트워크 재요청 없이 정확한 입력 계보를 재현할 수 있으며, 재시작 시 동일한 판단을 다시 계산할 수 있다.

### 3.2 기각: 매 완료봉마다 Alpaca REST 직접 호출

구현은 단순하지만 rate limit, 응답 시점, 재시도에 따라 동일 완료봉의 입력이 달라질 수 있다. 수집과 연구 실행의
장애 경계도 합쳐지므로 기각한다.

### 3.3 기각: 로컬 우선, REST fallback

장애 시 자동 복구처럼 보이지만 한 tick 안에 서로 다른 source lineage를 섞는다. 어떤 경로가 결과를 만들었는지와
replay 의미가 불명확해지므로 기각한다. 로컬 증거가 부족하면 명시적으로 BLOCKED가 맞다.

## 4. 운영 흐름

```text
기존 US collectors
  ├─ completed minute bars ──> MarketDataRuntimeStore
  ├─ bounded quote receipts ─> AlpacaSipDynamicReceiptStore
  └─ prior daily bars ───────> Alpaca daily cache
                                     │
외부 minute scheduler                ▼
  └─ one-shot snapshot orchestrator
       1. 현재 XNYS session과 effective policy 확인
       2. checkpoint/epoch/gap/sequence 확인
       3. 최신 completed bar receipt와 session minute ordinal 확인
       4. 기존 같은 minute tick이면 immutable artifact를 재사용
       5. 새 minute이면 bounded quote history와 daily reference 읽기
       6. 직전 immutable tick의 bars + 현재 BarFrame으로 canonical tick 구성
       7. canonical tick을 private immutable artifact로 발행
       8. 기존 Forward Shadow one-tick controller 호출
                                     │
                                     ▼
                    ForwardTrial events + Shadow artifacts
```

외부 스케줄러는 1분마다 실행할 수 있지만, orchestrator는 새 완료봉이 없으면 `REPLAYED` 또는
`BLOCKED/no_new_completed_bar`를 반환한다. 스케줄러의 중복 실행이 trial 수나 event 수를 늘리지 않는다.

## 5. 입력 계약

### 5.1 `UsForwardShadowSnapshotRequest`

요청은 경로와 identity만 전달하고 시장 값 자체를 전달하지 않는다.

- `source_id`
- `bar_connection_epoch`
- `instrument_id`
- `symbol`
- `policy_id`
- `session_date`
- `calendar_snapshot_id`
- `market_data_store_path`
- `daily_cache_path`
- `dynamic_plan_store_path`
- `dynamic_receipt_store_path`
- `tick_artifact_root`
- `max_slippage_bps`

`observed_at`은 요청 JSON에서 신뢰하지 않는다. library entry point는 테스트 가능한 injected clock을 받고, CLI는
timezone-aware system clock을 한 번 읽어 그 값을 사용한다. 모든 입력 path는 local path여야 하고 regular file/root의
owner, mode, symlink 정책을 각 기존 private store 계약으로 검증한다.

### 5.2 완료봉

`MarketDataRuntimeReader`에 다음 최소 read-only query를 공개한다.

- `latest_checkpoint(source_id)`
- `completed_bar_receipts(source_id, connection_epoch, instrument_id)`

두 query는 SQLite `mode=ro`와 `PRAGMA query_only=ON`을 사용한다. orchestrator는 writer lease를 얻지 않는다.
receipt 전체를 반환하는 이유는 bar 값뿐 아니라 `sequence`, `receipt_id`, `received_at`, `symbol` 계보가 필요하기
때문이다.

허용 조건은 다음과 같다.

- checkpoint가 존재하고 `gap_blocked == false`
- checkpoint epoch와 요청 bar epoch가 동일
- source 전체 receipt sequence가 strictly increasing이며 latest symbol receipt sequence가 checkpoint를 넘지 않음
- 모든 receipt의 source, epoch, instrument, symbol이 요청과 동일
- 선택한 latest receipt bar가 요청 session의 정규장에 속함
- 마지막 bar는 현재 시각보다 미래가 아니고 완료봉 freshness 한도 90초 이내
- 다른 날짜의 receipt는 warm-up에 섞지 않음

provider receipt sequence는 여러 symbol 사이에서 공유될 수 있으므로 `completed_bar_sequence`로 사용하지 않는다.
실험 sequence는 공식 XNYS 정규장 시작 이후 완료된 minute ordinal로 계산한다. 예를 들어 09:31 종료 봉은 1,
09:32 종료 봉은 2다. 따라서 스케줄러나 source가 한 분을 놓치면 다음 tick의 sequence가 실제로 점프하고 기존
ForwardTrial이 이를 `CENSORED/completed_bar_gap`으로 처리할 수 있다.

과거 세션 receipt는 indicator warm-up에도 사용하지 않는다. raw 완료봉에는 그 시점의 spread가 없으므로 현재 quote를
과거 봉에 복사하지 않는다. warm-up bars는 이 orchestrator가 같은 세션에서 앞선 minute에 발행한 immutable tick의
마지막 `BarFrame`만 이어 붙인다. 운영 시작 전 과거 분을 소급 생성하지 않는다.

### 5.3 quote

`AlpacaSipDynamicPlanStore.latest()`와 해당 계획에 결합된 bounded dynamic receipt store를 읽는다. 기존
`materialize_alpaca_sip_dynamic_quote_history_as_of()`를 사용해 raw receipt부터 quote state를 재구성한다.

허용 조건은 다음과 같다.

- plan market date가 요청 session date와 동일
- plan binding에 요청 instrument/symbol 쌍이 정확히 한 번 존재
- receipt history가 raw-first 검증을 통과하고 bounded terminal까지 complete
- quote의 connection epoch들이 해당 dynamic plan의 검증된 bounded history에 모두 결합됨
- 최신 quote의 instrument/symbol이 완료봉과 일치
- `quote.received_at <= observed_at <= quote.received_at + 5초`
- bid와 ask가 양수이고 `bid <= ask`
- 계산된 spread가 `max_slippage_bps` 이하

bar collector의 epoch와 dynamic quote collector의 epoch는 서로 다른 namespace다. 둘을 같은 값으로 비교하지 않고,
각 저장소 안에서 자기 plan/checkpoint와의 결합만 검증한다. quote의 event time, received time, event ID, plan ID와
connection epochs는 evidence로 남긴다. 단순히 DB의 마지막 bid/ask 두 숫자만 복사하지 않는다.

### 5.4 daily reference

기존 완료 상태 Alpaca daily cache에서 요청 session 이전 데이터만 대상으로 해당 symbol 하나를 읽는다.

- `prior_session < session_date`
- `prior_close > 0`
- `average_volume > 0`
- cache metadata가 요청 session을 평가할 수 있는 완료 범위임

daily reference가 없으면 0, 현재가 또는 임의 기본값을 넣지 않고 BLOCKED 처리한다.

### 5.5 catalyst와 candidate

이번 수직선은 뉴스 문장을 새로 생성하지 않는다. `BarFrame.catalyst`는 빈 문자열로 고정한다. 향후 catalyst evidence
연결은 별도 설계에서 point-in-time artifact를 추가해야 하며, 이 변경에 임의 문자열 fallback을 넣지 않는다.

각 tick의 `CandidateFrame`은 같은 session의 검증된 raw completed-bar receipts와 daily reference에서 결정론적으로
투영한다. raw receipts는 candidate 집계에는 사용할 수 있지만, 당시 spread가 없으므로 generated-strategy warm-up
`BarFrame`에는 직접 사용하지 않는다.

- `price`: latest close
- `gap_pct`: session 첫 raw bar open / prior close - 1
- `change_pct`: latest close / prior close - 1
- `relative_volume`: session raw bars 누적 volume / average daily volume
- `cumulative_dollar_volume`: session raw bars의 close × volume 합
- `spread_bps`: 현재 quote의 계산 spread
- `catalyst`: 빈 문자열

이 candidate는 종목 추천이나 주문 승인이 아니라 generated capsule에 전달하는 current-session feature frame이다.

## 6. canonical tick과 artifact identity

새 minute의 `CompletedMinuteBar`는 다음처럼 `BarFrame`으로 투영한다.

- `timestamp = bar.end_at`
- OHLCV = receipt의 완료봉 값
- `prior_close`, `average_daily_volume` = 검증된 daily reference
- `spread_bps` = 해당 minute tick을 최초 생성할 때 검증한 quote spread
- `catalyst = ""`

이전 immutable tick이 바로 직전 minute ordinal이면 그 tick의 `bars` 뒤에 현재 frame을 붙인다. 직전 tick이 없으면
현재 frame 하나로 시작한다. 이전 tick과 현재 tick 사이에 minute gap이 있으면 현재 frame 하나만 사용하며,
sequence 점프가 기존 trial을 censor하도록 한다. 이 방식은 각 과거 bar의 당시 spread를 보존하고 현재 quote의
look-ahead 복사를 막는다.

`completed_bar_sequence`는 공식 session minute ordinal이다. `completed_bar_id`는 기존
`completed_bar_id(latest_bar_frame)`를 사용한다. provider receipt sequence와 ID는 evidence에 별도로 보존한다.
evidence refs는 canonical ID로 정렬하고 중복을 금지한다.

tick artifact identity는 canonical serialized `UsForwardShadowTick`의 SHA-256이다. 저장 경로는
`<tick_artifact_root>/<session_id>/<instrument_sha256>/<minute_ordinal>.json`이며 root와 파일은 private immutable
계약을 따른다. 검증 전 외부 identifier를 path segment로 사용하지 않는다. path key를 source receipt ID나 contextual
`completed_bar_id`가 아니라 공식 minute ordinal로 잡아 같은 시장 minute에 수정 receipt나 뒤늦은 quote가 두 번째
실험 tick을 만들지 못하게 한다.

- 같은 minute, 같은 source receipt lineage: 기존 artifact를 읽어 exact replay
- 같은 minute, 다른 source receipt 또는 다른 새 bytes: correction/tamper conflict로 BLOCKED
- symlink, wrong owner/mode, malformed JSON: BLOCKED

최초 tick이 발행된 뒤 같은 minute에 더 최신 quote가 도착해도 기존 tick을 다시 계산하지 않는다. 기존 artifact의
source receipt, policy, session lineage가 현재 요청과 같으면 그 artifact를 재사용한다. 이는 한 완료봉의 실험 입력을
나중 데이터로 바꾸거나 같은 bar에 두 번 실험하는 것을 막는다.

## 7. 결과와 상태

`UsForwardShadowOrchestrationResult`는 다음 상태 중 하나를 반환한다.

- `READY`: 새 tick artifact를 발행하고 Forward Shadow 실행 완료
- `REPLAYED`: 동일 tick artifact와 기존 runtime 결과를 멱등 재확인
- `BLOCKED`: tick 미발행, ForwardTrial/event/outcome 변경 없음

`BLOCKED` reason code는 최소 다음을 구분한다.

- `session_closed`
- `wrong_session`
- `policy_mismatch`
- `checkpoint_missing`
- `connection_epoch_mismatch`
- `sequence_gap`
- `bar_missing`
- `bar_stale`
- `bar_lineage_mismatch`
- `daily_reference_missing`
- `dynamic_plan_missing`
- `quote_history_incomplete`
- `quote_missing`
- `quote_stale`
- `spread_too_wide`
- `artifact_conflict`
- `source_invalid`

결과에는 secret, raw authentication response 또는 raw quote payload를 넣지 않는다. READY/REPLAYED에는
`tick_artifact_id`, `completed_bar_id`, `policy_id`, `session_id`와 기존 `UsForwardShadowTickResult`만 포함한다.

## 8. fail-closed 순서

검증과 mutation의 순서는 고정한다.

1. 요청 shape와 local path 검증
2. system clock과 XNYS session 검증
3. effective US ExplorationPolicy 확인
4. market-data checkpoint, latest receipt와 공식 minute ordinal 검증
5. 동일 minute tick artifact가 있으면 source/policy lineage 검증 후 exact replay
6. 새 minute이면 daily reference 검증
7. dynamic plan, bounded receipt history와 quote 검증
8. 직전 immutable tick과 현재 frame으로 canonical tick 생성 및 model 재검증
9. tick private immutable 발행
10. 기존 Forward Shadow controller 호출

1~8 중 실패하면 어떤 ForwardTrial도 등록하지 않는다. 9에서 conflict가 나도 controller를 호출하지 않는다.
10에서 sandbox/capsule 실행이 실패하면 기존 Forward Shadow ledger 규칙에 따라 FAILED/BLOCKED event를 남긴다.

## 9. 스케줄러와 CLI

새 CLI는 `run_us_forward_shadow_from_market_data.py`로 한다.

```text
run_us_forward_shadow_from_market_data.py \
  --request /private/path/request.json \
  --ledger /private/path/experiment-ledger.sqlite3 \
  --generated-root /private/path/generated \
  --shadow-artifact-root /private/path/shadow \
  --result /private/path/latest-result.json
```

요청 파일에는 provider credentials나 URL이 없다. CLI는 한 번 실행하고 종료하며 daemon, retry loop 또는 자체 sleep을
소유하지 않는다. scheduler가 다음 완료봉에 다시 호출한다. stdout에는 구조화된 요약만 출력하고 raw payload나 path
내 secret 가능 문자열을 출력하지 않는다.

종료 코드는 다음으로 고정한다.

- `0`: READY 또는 REPLAYED
- `2`: malformed CLI/request
- `3`: 정상적인 fail-closed BLOCKED
- `1`: 예상하지 못한 내부 실패

## 10. 재시작과 동시성

- market-data, daily, dynamic receipt 입력은 read-only로 연다.
- tick artifact publication은 기존 private immutable primitive를 사용한다.
- experiment ledger의 기존 단일 writer lease를 그대로 사용한다.
- 같은 session/instrument/minute에 대한 동시 실행은 minute-keyed artifact와 ledger unique identity로 하나만 새 상태를 만들 수 있다.
- exact replay는 signal, entry, observed, exit, censored, outcome을 중복하지 않는다.
- 다음 완료봉은 이전 tick artifact를 수정하지 않고 새 artifact를 추가한다.
- capsule 실행은 기존 정책 선택 순서대로 최대 3개를 순차 실행한다.

## 11. 테스트와 관찰 가능한 완료 조건

테스트는 mock dict가 아니라 실제 임시 SQLite stores와 private artifact directory를 사용한다. 외부 network와 provider
credential은 사용하지 않는다.

### 계약 테스트

- reader가 checkpoint와 full receipt lineage를 read-only로 반환
- current-session latest receipt를 선택하고 official minute ordinal 계산
- 이전 immutable ticks에서 point-in-time `BarFrame` warm-up을 누적
- daily reference로 prior close와 ADV를 채움
- bounded quote receipts에서 exact bid/ask/spread와 evidence 생성
- candidate feature 계산이 결정론적
- 같은 minute의 같은 source lineage가 기존 byte-identical tick과 같은 artifact ID를 재사용

### 차단 테스트

- closed/wrong session
- missing checkpoint, wrong bar epoch, source sequence gap
- missing/stale/future/cross-symbol bar
- incomplete/missing/stale quote, wide spread
- missing/incomplete daily reference
- wrong policy, malformed/symlink/wrong-mode store
- artifact path collision/tamper

모든 차단 사례는 ForwardTrial과 event count가 변하지 않음을 증명한다.

### 통합 테스트

실제 local stores에 current-session bars, complete daily reference와 bounded quote receipts를 기록한 뒤 CLI를 실행한다.

1. 첫 실행이 READY이고 private tick artifact와 future-only trial을 만든다.
2. 같은 명령 재실행이 REPLAYED이고 ledger/event/artifact count가 동일하다.
3. 다음 완료봉과 그 시점 quote를 추가한 뒤 실행하면 정확히 새 tick 하나와 다음 forward observation만 추가한다.
4. 프로세스를 재시작해도 같은 결과가 유지된다.
5. 한 minute를 건너뛴 다음 실행하면 provider sequence가 아니라 official minute ordinal 점프로 trial이 censor된다.

### 필수 검증

- 변경 Python 파일 대상 Ruff
- 변경 Python 파일 대상 basedpyright
- 새 snapshot/orchestration tests
- 기존 US Forward Shadow targeted tests
- CLI `--help`
- malformed request
- 실제 임시 local stores를 사용한 happy path와 replay 수동 실행
- import closure에서 broker/order/account/position/credential/network 모듈 부재 확인

## 12. 보안 및 권한 불변식

- trading URL, Alpaca credential 또는 HTTP client를 새 코드에 받거나 import하지 않는다.
- Alpaca live endpoint는 어떤 형태로도 추가하지 않는다.
- paper endpoint도 이번 read-only 수직선에서는 호출하지 않는다.
- KIS·LS mutation 경로를 추가하지 않는다.
- input/output private file은 symlink와 불안전한 mode를 거부한다.
- raw SIP payload는 기존 receipt store 밖으로 복제하지 않는다.
- replay/backtest/synthetic/Shadow 수익을 실제 수익성으로 표현하지 않는다.

## 13. 자체 검토 결과

- **결정되지 않은 fallback:** 없음. 로컬 증거 부족은 모두 BLOCKED다.
- **시간 의미:** system clock의 현재 XNYS 세션과 latest completed bar만 허용한다.
- **계보 의미:** bars, quote, daily reference, policy와 각 source의 독립 epoch를 별도 evidence로 보존한다.
- **look-ahead 방지:** 과거 raw bar에 현재 spread를 복사하지 않고, 앞선 immutable tick의 당시 frame만 warm-up한다.
- **재현성:** 입력 store가 append-only이고 tick은 immutable/content-addressed다.
- **권한 누수:** 새 경로는 read-only source projection과 research-only runtime만 소유한다.
- **다음 단계 경계:** 이 명세 승인 뒤에만 TDD 구현 계획을 작성하고 코드를 변경한다.
