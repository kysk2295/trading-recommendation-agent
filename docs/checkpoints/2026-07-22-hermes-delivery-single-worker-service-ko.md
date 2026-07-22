# Hermes Delivery Single-Worker Service 체크포인트

작성 기준 커밋: `c164e1366a33d27dd7c1201696d57ea34de6ca1c`

## 판정

- gateway plugin이 실제로 load될 때만 daemon thread가 생기던 수명주기 의존성을 제거했다.
- Hermes Python 3.11에서 직접 실행되는 foreground delivery service를 launchd `KeepAlive`로 배치했다.
- process 전체 수명의 mode-`600` OS lease가 두 번째 worker를 DB claim 전에 차단한다.
- 서비스는 실제 배치와 강제 재시작 뒤에도 실행되지만 Telegram acknowledgement는 아직 0건이다.
- 따라서 항상 실행되는 single-worker 운영 조건은 충족했지만 Milestone 1 전체는 미완료다.

## 구현 계약

`integrations.hermes/trading-agent/service.py`는 세 command만 제공한다.

- `run`: 기존 Hermes sender와 append-only delivery store를 사용하는 foreground loop
- `provision`: credential 값 없이 mode-`600` LaunchAgent plist 생성
- `verify`: 현재 project, Hermes venv, profile, database와 plist의 exact 계약 확인

LaunchAgent에는 `HOME`, `HERMES_HOME`, `PATH`, `VIRTUAL_ENV` 경로만 들어간다. Telegram token, channel ID,
broker, account, order 또는 임의 endpoint 입력은 없다. sender가 전역 Hermes env를 먼저 읽고 stockagent profile
env를 나중에 읽어 profile token 우선권과 global home-channel fallback을 보존한다. 값은 출력하지 않는다.

legacy `TRADING_AGENT_HERMES_DELIVERY_ENABLED`는 stockagent profile에서 false로 전환했다. gateway process는
중단하거나 재시작하지 않았고, 독립 서비스만 delivery database의 process-lifetime lease를 소유한다.

## 실제 배치 증거

- LaunchAgent label: `ai.trading-agent.hermes-delivery`
- 상태: `running`
- 실행: Hermes venv Python 3.11의 module mode
- working directory: active `trading-recommendation-agent` checkout
- plist mode/owner: `600`, current UID
- service lock mode: `600`
- 첫 service run: `runs=1`, 30초 이상 PID 유지
- 강제 service-only restart: PID 교체, `runs=2`, 새 process가 lease 재획득
- stockagent gateway: 기존 process 유지

재시작 전후 production delivery store의 event는 1건이었다. 기존 incident는 총 attempt 3건 뒤
`telegram_timeout` dead letter 1건이 되었고 acknowledgement는 0건이다. 재시작 뒤 attempt와 dead letter가
증가하지 않아 terminal event를 중복 claim하지 않았다.

## 검증

- 실패 우선: foreground service, lifetime lease와 service CLI 부재로 신규 4개 테스트 실패
- Hermes service/plugin 집중 회귀: 21 passed
- Python 3.11 actual `--help`: `run`, `provision`, `verify` 확인
- CLI 오입력: relative database exit `2`, redacted blocked result
- fixture service: event 1건 전송·acknowledgement 1건·lease release
- 동시 service: 두 번째 process owner가 lifetime lease에서 차단
- actual provision/verify: mode-`600`, secret-free plist exact match
- actual launchd: running, service-only restart와 lease reacquisition 확인
- 전체 pytest: **3247 passed in 180.39s**
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings, 0 notes
- compileall 및 Python 3.11 py_compile: 통과
- no-excuse 검사: 통과

## 다음 M1 증거

1. Telegram network가 정상인 시점에 새 current 추천 또는 명시적 무추천 event를 production 원장에 투영한다.
2. launchd service가 acknowledgement 1건을 기록하고 Hermes/Telegram에서 같은 내용을 확인한다.
3. 동일 event replay와 service restart가 추가 Telegram message를 만들지 않는지 대사한다.

실자금 endpoint, Alpaca live endpoint, KIS·LS 주문 endpoint와 계좌·주문 mutation은 사용하지 않았다.
