# Alpaca SIP bounded reconnect supervisor 체크포인트

## 계약

- 같은 market date와 symbol의 failed connection attempt와 terminal session 합계를 durable budget으로 사용한다.
- 최대 connection 수는 1~3이며 fixture 기본값은 3이다.
- transport, opening handshake, provider 500 internal error와 준비된 session의 transport disconnect만 재시도한다.
- 402 auth failure, 406 connection limit, 409 insufficient subscription, endpoint·protocol·기타 provider rejection은 즉시 중단한다.
- backoff는 1초, 2초로 bounded하며 sleeper를 주입해 테스트에서 실제 대기하지 않는다.
- 매 operation 실패는 기존 원장 prefix가 유지되고 새 evidence가 정확히 하나 추가되어야 한다.
- 프로세스 재시작은 원장에 이미 소비된 budget을 다시 읽고, 소진됐으면 connector를 열지 않는다.
- 최신 evidence가 bounded complete면 operation 0회로 성공을 재사용한다.
- 실패 epoch 뒤 성공 epoch가 있어도 `continuity_attested=false`다.
- shutdown 요청은 새 operation과 retry backoff 전에 확인하고 `graceful_shutdown`으로 종료한다.
- 실행마다 명시적 64자리 `run_id`를 사용해 started, connecting, retry scheduled와 terminal 상태를 별도 감사 원장에 남긴다.
- 감사 이벤트는 sequence와 previous event ID를 가진 content hash chain이며 mode 600 append-only SQLite에서 재검증한다.
- 동일 `run_id` 재사용, row/payload 변조, schema·mode·소유자·symlink 위반은 fail-closed 한다.

## 로컬 QA

```bash
uv run python run_alpaca_sip_trade_stream_supervisor_fixture.py \
  --state-dir outputs/alpaca-sip-supervisor-fixture \
  --run-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

- help: exit 0
- invalid run-id: exit 2, sanitized error
- first run: operation 2, attempt 1, terminal 1, audit event 5, sleep 1초, READY, network 0
- 새 run-id 재실행: operation 0, audit event 2, sleep 0, connection total 2, READY, network 0
- 새 state와 `--shutdown-before-operation`: operation 0, audit event 2, STOPPED, network 0

## 검증

- supervisor targeted: 10 passed
- full repository: 2386 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- changed-file format, compileall, no-excuse: passed
- actual WebSocket, account/order endpoint와 broker mutation: 0건

## 남은 단계

- 열린 NYSE 정규장에서 one-shot actual read-only smoke를 먼저 대사
- 그 증거 뒤 production smoke에 같은 bounded policy를 opt-in으로 연결
- 장중 장기 soak에서 실제 signal 기반 graceful shutdown, heartbeat와 provider subscription ownership 검증
