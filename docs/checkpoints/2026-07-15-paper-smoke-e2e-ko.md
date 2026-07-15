# Intraday Paper smoke fake broker E2E 체크포인트

확인일: 2026-07-15

상태: **같은 운영 세션 API를 통한 전 수명주기 fake broker E2E 추가, 실제 Alpaca Paper POST/DELETE 0건 유지**

## 목적

정규장·자격증명·현재 ORB 후보가 없는 때에도 실제 주문을 억지로 만들지 않고, intraday Paper smoke의 정상 수명주기 전체를 하나의 현재-epoch fixture로 검증한다. 이 단계는 실제 Alpaca Paper 계정, 네트워크 또는 credential 파일을 열지 않는다.

## 검증한 수명주기

`tests/test_paper_smoke_e2e.py`는 `PaperOperatingSession` 공개 API와 단일 append-only `ExecutionStore`를 사용한다.

1. current completed bar와 exact arm으로 축소 entry를 한 번 실행한다.
2. 실제 `trade_updates` 파서가 읽는 filled event를 원장에 append하고, terminal entry REST snapshot과 broker position을 current epoch에 다시 대사한다.
3. 같은 parent intent에 exact 수량의 보호 OCO를 제출하고 immutable OCO plan·broker OCO를 대사한다.
4. 15:55 ET의 EOD safety 호출은 보호 OCO cancel만 승인하고, close를 같은 호출에 섞지 않는다.
5. cancel terminal REST 대사 뒤 15:56 ET의 새 safety 호출이 exact position close만 승인한다.
6. 마지막 broker open order 0·position 0 snapshot과 shadow order projection을 `reconcile_paper_state()`로 대사한다.

fixture는 entry filled REST 상태를 `recent_orders`로 제공한다. 따라서 최초 accepted recovery가 이후 append-only fill보다 뒤처졌다면 protection을 fail-closed하는 실제 REST recovery 계약도 함께 검증한다.

## 보존한 안전 경계

- Alpaca Paper mutation endpoint를 호출하지 않는다. fake credential과 in-process fake broker만 사용한다.
- 최대 notional 100 USD, 계획위험 10 USD, 최대 1 포지션, 일손실 30 USD, 편도 20bp의 pilot contract를 바꾸지 않는다.
- `PaperMutationArm`, single Writer, REST/WSS current-epoch barrier, OCO cancel 뒤 재대사, final broker/shadow reconciliation을 우회하지 않는다.
- timeout·거절·부분체결 resize·재시작 recovery의 fail-closed 동작은 기존 독립 회귀에서 계속 검증한다. 이 E2E는 정상 full-fill smoke 수명주기의 결합 증거이며 성과나 수익성 증거가 아니다.

## 검증 근거

- 새 E2E와 entry/OCO/safety/reconciliation 관련 회귀: `33 passed`
- 전체 회귀: `946 passed`
- `uv run ruff check .` 및 새 테스트 `ruff format --check`: 통과
- `uv run basedpyright`: 오류 0, 경고 0, notes 0
- `git diff --check`: 통과
- 직접 CLI QA:
  - entry·보호 OCO·safety armed CLI의 `--help`: 모두 종료코드 0
  - 세 CLI의 잘못된 arm: 모두 종료코드 2, 지정 execution DB·output directory 미생성
  - entry CLI의 유효 arm + 없는 execution ledger: 종료코드 1, execution DB 미생성. credential·broker adapter를 열지 않음
- 외부 Alpaca Paper POST/DELETE: 0건

## 다음 실제 게이트

이 테스트는 정규장 smoke를 대체하지 않는다. 열린 정규장에서 credential, Paper base URL, account fingerprint, current completed 1분 ORB source, 현재 REST/WSS 대사가 모두 충족될 때에만 runbook 순서대로 축소 entry 1건을 실행한다. 하나라도 없으면 fake fixture 검증만 유지하며 broker mutation을 만들지 않는다.
