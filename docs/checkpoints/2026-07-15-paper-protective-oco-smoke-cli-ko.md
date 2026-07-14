# Alpaca Paper 보호 OCO armed smoke CLI 체크포인트

확인일: 2026-07-15

상태: **current-epoch 보호 OCO smoke CLI 추가, 실제 Paper POST 0건 유지**

## 목적

신규 entry smoke가 실제 Paper 체결을 만들 수 있게 열린 뒤에는, 체결 노출을 오래 방치하지 않고 같은 운영 세션 안전 경계에서 보호 OCO를 제출할 수 있어야 한다. 이번 단계는 이미 체결된 parent intent 하나를 명시적으로 지정해 `DAY` OCO stop-market + 2R limit 보호 주문을 실행하는 축소 smoke CLI를 추가했다.

이 CLI는 전략 pilot이나 수익성 증거가 아니다. 정규장, current-epoch WSS/REST 복구, 체결 원장, broker 포지션, 보호 계획과 계좌 fingerprint가 모두 맞을 때만 기존 mutation executor를 호출한다.

## 구현 경계

- `run_alpaca_paper_protective_oco_smoke.py`를 추가했다.
- 실행에는 정확한 `--arm-paper-mutation ARM_ALPACA_PAPER_ONLY` 값과 `--intent-id`가 필요하다.
- 실행 원장이 초기화되지 않았으면 자격증명 로딩이나 broker 세션 전에 차단 보고서를 쓴다.
- `PaperOperatingSession.execute_protective_oco()`만 호출하며 신규 entry 주문은 만들지 않는다.
- 결과 보고서는 `paper_protective_oco_smoke_ko.md`에 차단, noop, ack, ambiguous/rejected 상태를 남긴다.
- ACK 또는 이미 ACK된 보호 OCO만 종료코드 0으로 본다. 차단은 1, 모호·거부·예외는 2로 둔다.

## 검증

- 새 CLI 단위 회귀: `4 passed`
- 보호 OCO 운영 세션 관련 표적 회귀: `13 passed`
- 전체 회귀: `574 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: 오류·경고 0
- CLI 수동 QA:
  - `./run_alpaca_paper_protective_oco_smoke.py --help`: 종료코드 0
  - 잘못된 arm 값: argparse에서 종료코드 2
  - fixture 기반 happy path: ACK 보고서 생성

## 실제 Paper 상태

이번 단계에서 Alpaca Paper POST/DELETE는 실행하지 않았다. 실제 정규장 최소 entry가 체결된 뒤, 같은 parent intent로 이 CLI를 실행해 보호 OCO ACK, `trade_updates`, REST nested OCO, 원장 대사를 함께 확인해야 한다.

장이 닫혀 있거나 current-epoch 안전 조건이 부족하면 실제 POST를 억지로 실행하지 않고 차단 보고서만 남긴다.

## 다음 안전 게이트

1. 열린 정규장에서 축소 entry smoke 1건을 실제 Paper로 제출한다.
2. 체결 또는 부분체결 확인 즉시 이 CLI로 보호 OCO를 제출한다.
3. OCO ACK, WSS, REST nested OCO, Account Activities와 원장을 대사한다.
4. 보호 OCO가 확인되기 전에는 신규 entry admission을 계속 fail-closed한다.
5. 이후 cancel/replace와 EOD 평탄화 smoke를 별도 체크포인트로 검증한다.
