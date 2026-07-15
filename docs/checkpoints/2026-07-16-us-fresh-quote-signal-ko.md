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

실패는 `market_closed`, `provider_failed`, `invalid_quote`, `future_quote`, `stale_quote`, `spread_too_wide`, `setup_invalidated`, `entry_slippage_exceeded` 중 하나로 확정한다. 원래 conditional 신호를 덮어쓰거나 synthetic quote, 외부 전송, 주문 fallback을 만들지 않는다.

## 불변 산출물

```text
opportunities.v1.jsonl
trade-signals.v1.jsonl (conditional 먼저)
us-quote-snapshots.v1.jsonl
trade-signals.v1.jsonl (validated 신호)
trade-signal-cards-ko/
quote-actionability-assessments.v1.jsonl
```

quote, assessment, derived signal은 canonical SHA-256 ID를 사용한다. exact replay는 no-op이며 같은 ID의 다른 payload와 malformed 기존 JSONL은 fail-closed한다. conditional 카드의 기존 내용은 바이트 단위 회귀 테스트로 고정했다.

## 검증

- focused fresh-quote suite: `62 passed`
- 전체 pytest: `1400 passed in 29.98s`
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
