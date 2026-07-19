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

## 로컬 QA

```bash
uv run python run_alpaca_sip_trade_stream_supervisor_fixture.py \
  --state-dir outputs/alpaca-sip-supervisor-fixture
```

- help: exit 0
- missing state-dir: exit 2
- first run: operation 2, attempt 1, terminal 1, sleep 1초, READY, network 0
- exact replay: operation 0, sleep 0, connection total 2, READY, network 0

## 검증

- SIP targeted: 55 passed
- full repository: 2381 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- changed-file format, compileall, no-excuse: passed
- actual WebSocket, account/order endpoint와 broker mutation: 0건

## 남은 단계

- 열린 NYSE 정규장에서 one-shot actual read-only smoke를 먼저 대사
- 그 증거 뒤 production smoke에 같은 bounded policy를 opt-in으로 연결
- 장중 장기 soak에서 heartbeat, graceful shutdown과 provider subscription ownership 검증
