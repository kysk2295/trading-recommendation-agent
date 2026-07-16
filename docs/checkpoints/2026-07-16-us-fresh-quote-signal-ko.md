# 미국 현재 호가 검증 신호 체크포인트

날짜: `2026-07-16 KST`

## 결과

- KIS 미국주식 1호가 read-only adapter: 구현
- endpoint: `GET /uapi/overseas-price/v1/quotations/inquire-asking-price`
- transaction ID: `HHDFS76200100`
- conditional 신호별 quote actionability 평가: 구현
- 동일 종목 batch 조회: cycle당 1회
- immutable quote snapshot·terminal assessment·derived signal: 구현
- 외부 메시지 전송: 0건
- KIS 계좌·잔고·포지션·주문 호출: 0건
- Alpaca Paper 및 실제 자금 mutation: 0건

이 결과는 현재 호가를 근거로 Paper forward-validation 후보를 구분하는 계약 구현 증거다. 체결, 수익성, 전략 승격 또는 주문 승인 증거가 아니다.

## 인과성과 위험 게이트

평가 순서는 base 신호 유효성·현재 정규장, provider 시각·날짜·세션, 엄격한 `<5초` freshness, `25bp` spread, stop 무효화, entry 대비 `20bp` slippage다. 모든 조건을 통과한 long 신호만 ask가 entry 아래면 `validated_waiting`, entry 이상이면 `validated_trigger_reached`로 새 immutable 신호를 만든다.

실패는 `market_closed`, `provider_failed`, `future_quote`, `stale_quote`, `spread_too_wide`, `setup_invalidated`, `entry_slippage_exceeded` 중 하나로 확정한다. malformed·symbol mismatch provider 호가도 snapshot 없는 canonical `provider_failed`로 축약해 서로 바꿔치기 가능한 terminal 상태를 남기지 않는다. 원래 conditional 신호를 덮어쓰거나 synthetic quote, 외부 전송, 주문 fallback을 만들지 않는다.

## 불변 산출물

```text
opportunities.v1.jsonl
trade-signals.v1.jsonl (conditional 먼저)
us-quote-snapshots.v2.jsonl
trade-signals.v1.jsonl (validated 신호)
trade-signal-cards-ko/
quote-actionability-assessments.v2.jsonl
```

quote, assessment, derived signal은 canonical SHA-256 ID를 사용한다. exact replay는 no-op이며 같은 ID의 다른 payload와 malformed 기존 JSONL은 fail-closed한다. conditional 카드의 기존 내용은 바이트 단위 회귀 테스트로 고정했다.

독립 리뷰 뒤 quote ID에 로컬 `received_at`을 포함해 같은 provider 표시값의 별도 수신을 구분했다. assessment ID는 `(base_signal_id, scan_started_at)`으로 고정해 한 cycle의 두 번째 terminal payload를 conflict로 차단한다. 이 새 공식은 schema/file v2로 분리해 기존 v1 artifact를 읽거나 덮어쓰지 않는다. 임의 경로 standalone quote writer를 제거하고 일반 signal writer는 conditional만 허용한다. 단일 batch writer는 기존 signal outbox의 실제 base conditional을 먼저 조회해 lane·side·entry type·가격·stop·targets·rationale·opportunity·유효기간을 derived와 대조하고 ID 공식, snapshot 대비 quote validation 값, base·quote evidence ID·관측시각을 재검증한다. 모든 terminal status도 base current→정규장→quote→future/stale→spread→stop→slippage→waiting/reached 순서로 다시 계산한다. malformed·symbol mismatch quote는 snapshot 없는 canonical `provider_failed`로 축약하고, provider 요청 중 base 만료·장 종료가 발생하면 완료시각 preflight의 `setup_invalidated`·`market_closed`로 확정한다. 이미 기록된 v2 `invalid_quote` 행은 append-only 재생을 위해 읽기 호환을 유지하지만 신규 batch는 거부한다. 카드 디렉터리의 기존 non-directory 경로도 첫 append 전에 거부한다. 따라서 base가 없거나 달라진 경우, 불완전하거나 의미가 모순된 batch와 terminal conflict는 어떤 부분 산출물도 남기지 않는다. KIS client는 exact live·virtual-trading origin만 허용하고 전역·요청 단위 redirect를 모두 끄며, 모든 read-only GET과 같은 500/502/503/504 단일 bounded retry를 사용한다. retry 뒤 redirect·rate limit·transport error도 복구가 아닌 실패 audit으로 남긴다. 레거시 alert `queued_at`은 quote 평가 전 시각을 재사용하지 않고 실제 outbox append 직전에 캡처한다.

## 검증

- review-focused fresh-quote·KIS HTTP suite: `104 passed in 0.55s`
- 전체 pytest: `1436 passed in 21.01s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit 0
- `--top 0`: exit 2, credential 로딩·출력 생성 전 차단
- fake-provider happy path: conditional 1, snapshot 1, validated signal 1, assessment 1
- terminal 출력: aggregate count만 표시하고 symbol·price·quote ID·provider message 비노출

## 실제 read-only smoke 상태

점검 시각은 `2026-07-15 18:33 EDT`로 NYSE 정규장 종료 후였다. 로컬 `~/.config/trading-agent/kis.env`는 현재 사용자 소유 regular file, mode `600`이었지만 현재-session 조건을 우회하지 않았다. 따라서 이번 체크포인트의 실제 KIS 1호가 endpoint 요청은 0건이며 fake-provider E2E가 권위 있는 실행 검증이다.

다음 정규장에는 새 output directory와 `--top 1 --mode live --range-minutes 5 --max-pages 10 --strategy orb`로 한 번만 bounded read-only smoke를 실행한다. mid-session 시작은 적격 forward day로 세지 않고, 생성된 quote·assessment·signal의 aggregate count와 비밀정보 비노출만 확인한다.

## 다음 단계

1. 정규장 KIS current-quote bounded smoke
2. 명시적 destination allow-list와 durable acknowledgement를 가진 외부 delivery adapter
3. REST latency·coverage 근거가 필요할 때만 KIS quote WebSocket 검토
4. 별도 explicit arm 아래 Alpaca Paper entry/OCO/EOD 운영 체크포인트

실제 자금 거래와 KIS 주문 경로는 계속 범위 밖이다.
