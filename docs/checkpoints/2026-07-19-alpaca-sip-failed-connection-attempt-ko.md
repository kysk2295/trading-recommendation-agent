# Alpaca SIP failed connection attempt 체크포인트

## 목적

WebSocket session이 인증과 구독까지 도달하지 못해도 연결 시도 자체를 감사 가능하게 보존한다. 준비된 stream session의 terminal 의미는 변경하지 않는다.

## 근거

Alpaca 공식 [WebSocket Stream 문서](https://docs.alpaca.markets/docs/streaming-market-data)는 402 auth failed, 406 connection limit exceeded, 409 insufficient subscription을 정의한다. 구현은 숫자 code만 분류하며 provider `msg`나 exception text를 attempt row와 CLI report에 복제하지 않는다.

## 저장 계약

- stream audit schema v2에 append-only `connection_attempts` table과 update/delete 차단 trigger를 추가했다.
- 기존 schema v1은 query-only attestation/history를 계속 읽을 수 있다.
- 다음 Writer lease에서만 v1 object set을 검증한 뒤 v2 object를 추가한다.
- 기존 control, data link와 terminal session row는 migration에서 재작성하지 않는다.
- attempt는 connection epoch, symbol, market date, failed time, stage, failure code와 canonical content hash만 저장한다.
- `connect`, `endpoint`, `connected_control`, `authentication_control`, `subscription_control` stage별 허용 control count를 read-back에서 검증한다.
- 같은 epoch에 terminal session이 있거나 attempt hash·stage/control 범위가 맞지 않으면 fail-closed다.

## 실패 분류

- opening handshake: `handshake_failed`
- transport/timeout: `transport_failed`
- final endpoint mismatch: `endpoint_rejected`
- provider 402: `authentication_failed`
- provider 406: `connection_limit`
- provider 409: `insufficient_subscription`
- 그 밖의 provider/protocol: `provider_rejected` 또는 `protocol_invalid`

## 로컬 QA

```bash
uv run python run_alpaca_sip_trade_stream_attempt_fixture.py \
  --scenario connection-limit \
  --stream-store outputs/alpaca-sip-attempt/stream.sqlite3
```

- help: exit 0
- invalid scenario: exit 2, state/network 0
- connection-limit: exit 0, attempt 1, control 1, terminal 0, network 0
- handshake-failure: exit 0, attempt 1, control 0, terminal 0, network 0

## 검증

- SIP targeted: 50 passed
- full repository: 2376 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse: passed
- actual WebSocket connection, account/order endpoint와 broker mutation: 0건

## 다음 단계

- 열린 NYSE 정규장의 bounded actual SIP frame 1개와 private report 대사
- actual provider rejection 발생 시 raw control receipt와 sanitized attempt의 epoch 일치 검증
- bounded reconnect supervisor를 구현할 때 retry budget, backoff와 connection-limit no-retry 계약 추가
