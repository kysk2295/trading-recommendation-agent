# Signed Hermes Alpaca Paper Arm Gateway 체크포인트

## 완료 범위

- Hermes owner context는 raw Telegram 식별자를 저장하거나 전달하지 않고 SHA-256 owner binding으로 변환한다.
- `prepare`는 현재 session/lane의 단일 `PAPER_CHAMPION`, Alpaca Paper account binding, 고정 lane risk contract, 깨끗한 현재 commit을 검증한다.
- owner 확인은 5분 만료 HMAC confirmation으로 한 번만 가능하다.
- `consume`은 모든 authority binding을 다시 읽고 exact match일 때만 기존 `PaperMutationArm`을 한 번 반환한다.
- confirm, consume, revoke, expire transition은 mode `0600` SQLite에 서명된 append-only chain으로 기록된다.
- key는 기본 `~/.config/trading-agent/hermes-arm.env`에서 `O_NOFOLLOW`, current UID, regular file, exact mode `0600`, 단일 `HERMES_ARM_SIGNING_KEY` 계약으로 읽는다.
- CLI와 Hermes 응답은 key, owner binding, account fingerprint, nonce, signature를 출력하지 않는다.

## 실행 경계

```text
Hermes owner context
  -> trading_agent_arm_prepare
  -> signed request + confirmation
  -> trading_agent_arm_confirm
  -> coordinator-only consume
  -> PaperMutationArm (one use)
```

Gateway와 CLI는 broker, Alpaca HTTP client, live endpoint를 import하지 않는다. `consume` 자체는 주문을 만들지 않으며, 다음 operating coordinator가 기존 Alpaca Paper 전용 실행 경계에 arm 객체를 전달해야 한다.

기본 로컬 상태 경로는 다음과 같다.

- arm ledger: `outputs/hermes/arm.sqlite3`
- lane registry: `outputs/lane_control/lane_registry.sqlite3`
- experiment ledger: `outputs/experiment_control/experiment_ledger.sqlite3`
- signing key: `~/.config/trading-agent/hermes-arm.env`

## 검증

- exact prepare, confirm, consume와 consume replay 차단을 별도 CLI 프로세스로 확인했다.
- wrong owner/session/lane, risk/account/commit/champion mismatch, dirty repository, expiry, revoke, confirmation replay를 fail-closed 확인했다.
- signing key mode와 symlink 거부, redacted stdout, broker/HTTP import 부재를 확인했다.
- 실제 Alpaca Paper POST와 Telegram 전송은 수행하지 않았다.

## 다음 단계

Task 7 US Day Operating Coordinator가 이 gateway의 `consume` 결과를 기존 entry/protective OCO/EOD flatten/reconciliation 실행 수명주기에 연결한다. fake broker vertical을 먼저 완결한 뒤에만 명시적 owner arm을 사용하는 정규장 Paper smoke가 가능하다.
