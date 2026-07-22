# Hermes와 US Day 운영 제품 체크포인트

기준 시각: 2026-07-22 KST
기준 커밋: `f149bdecbfac4db205954f1bd497ee211851f472`

## 판정

- Hermes 플러그인 설치와 로컬 도구 실행 계약은 검증됐다.
- Telegram 외부 acknowledgement는 아직 검증되지 않았다.
- US Day 운영 코드는 준비됐지만 실제 정규장 Paper lifecycle 증거는 아직 없다.
- 따라서 AC-001과 AC-002는 모두 미완료다. Allocation gate도 열리지 않았다.

## 검증된 사실

### 전체 자동 게이트

- `uv run pytest -q`: **3237 passed in 189.23s**
- `uv run ruff check .`: 통과
- `uv run basedpyright`: **0 errors, 0 warnings, 0 notes**
- `uv run python -m compileall -q trading_agent integrations/hermes/trading-agent`: 통과

### CLI 수동 QA

다음 네 실행 파일에서 `--help`, fail-closed 오입력, fixture happy path를 확인했다.

- `run_acceptance_evidence.py`: AC-001 manifest build/verify 성공, criterion mismatch 차단, manifest mode `600`
- `run_hermes_delivery.py`: 빈 전용 store query 성공, malformed projection 차단
- `run_hermes_arm_gateway.py`: prepare/confirm/consume 일회성 흐름 성공, 잘못된 signing key 차단, owner/account/signature/nonce 비노출
- `run_us_day_operating_session.py`: 세 세션 fixture evidence bundle 생성 성공, 누락 terminal 차단

### Hermes stockagent 플러그인

- 설치 원본: `origin/main`의 `integrations/hermes/trading-agent`
- 설치 버전: `1.2.0`
- 설치 manifest SHA-256: `9500fc989ca69b92493baedc0d08974c8e2b8cff4850cf748bf71025b100a183`
- `stockagent` gateway는 재시작 후 실행 중이다.
- Hermes runtime registry에서 아래 다섯 도구가 실제 등록됐다.
  - `trading_agent_query`
  - `trading_agent_status`
  - `trading_agent_arm_prepare`
  - `trading_agent_arm_confirm`
  - `trading_agent_arm_revoke`
- 실제 registry dispatch의 `trading_agent_status`는 `ready`, plugin version `1.2.0`, Paper arm gateway 사용 가능을 반환했다.
- Hermes Python 3.11에서 플러그인 entrypoint가 파싱되지 않던 결함은 `f149bde`에서 수정하고 회귀 테스트를 추가했다.

## 아직 통과하지 못한 운영 증거

### Telegram acknowledgement

- Telegram token, home channel, 비어 있지 않은 owner allowlist가 구성돼 있고 wildcard allowlist가 아님을 값 노출 없이 확인했다.
- production delivery DB가 아닌 전용 mode-`600` QA SQLite store에 root/reply fixture를 등록했다.
- root fixture의 첫 외부 전송은 Telegram acknowledgement 없이 `retry_scheduled`로 끝났다.
- 중복 전송 가능성을 피하기 위해 같은 delivery를 즉시 재시도하지 않았고 reply도 보내지 않았다.
- platform message ID acknowledgement는 0건이므로 AC-001을 완료로 판정하지 않는다.

### US Day 실제 정규장 lifecycle

- 이 체크포인트 시점에는 NYSE 정규장이 닫혀 있어 실제 Paper POST를 강제로 만들지 않았다.
- 실제 Alpaca Paper mutation 누적은 계속 0건이다.
- 자연 발생 setup의 entry, protective OCO, flat, broker/shadow reconciliation, Hermes outcome acknowledgement가 한 세션에서 이어진 증거가 없다.
- 세 개의 실제 scheduled-session terminal도 아직 없으므로 AC-002 US subgate를 완료로 판정하지 않는다.

## 다음 운영 순서

1. Telegram 연결이 정상화된 뒤 기존 retry identity를 중복 없이 처리해 root와 reply의 서로 다른 platform message ID 두 건을 전용 QA store에 acknowledge한다.
2. 다음 NYSE 정규장 전에 clean pushed commit으로 preflight하고, 자연 발생 setup만 명시적 Paper arm 아래 실행한다.
3. setup이 없으면 threshold를 낮추지 않고 `censored_no_setup`으로 확정한다.
4. 실제 세 세션 terminal과 한 번의 자연 lifecycle이 모이면 AC-002 US subgate를 다시 검증한다.
5. Allocation Manager는 서로 독립된 executable Paper champion 두 개가 생기기 전까지 주문권한 없이 닫아 둔다.

실자금 endpoint, KIS/LS 주문 endpoint, 자격증명·계좌·Telegram 식별자는 사용하거나 기록하지 않았다.
