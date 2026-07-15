# Alpaca Paper cancel·EOD 평탄화 armed smoke CLI 체크포인트

확인일: 2026-07-15

상태: **current-epoch 안전조치 mutation smoke CLI 추가, 실제 Paper POST/DELETE 0건 유지**

## 목적

cutoff·일손실 kill switch·EOD 평탄화 계획을 로컬 원장에만 남기던 경계에서, 동일한 단일 Writer/WSS 운영 세션 안에서 계획된 cancel과 exact 정수 포지션 flatten을 순서대로 실행할 수 있는 축소 smoke CLI를 추가했다. 이 경계는 실제 자금 거래를 지원하지 않으며 정확한 Alpaca Paper endpoint만 사용할 수 있다.

이번 변경은 승인된 lane 아키텍처를 바꾸지 않는다. `intraday_momentum`의 A단계 안전 체크포인트 안에서 공개 mutation 표면을 강화한 것이며, lane control-plane 계약은 A/B 검증 뒤 점진적으로 도입한다.

## 구현 경계

- `run_alpaca_paper_safety_mutation_smoke.py`를 추가했다.
- 실행에는 정확한 `--arm-paper-mutation ARM_ALPACA_PAPER_ONLY`가 필요하다.
- 공개 `PaperOperatingSession`의 entry·보호 OCO·안전조치 실행은 `PaperMutationArm`을 필수 인자로 받고 runtime에도 정확한 타입·값을 다시 검증해 CLI 밖 우회 호출을 차단했다.
- 미초기화 원장은 자격증명 로딩과 운영 세션 시작 전에 차단한다.
- 실행 전 current-epoch REST/WSS recovery, account fingerprint, append-only mutation recovery를 모두 통과해야 한다.
- 계획을 만든 동일 REST broker snapshot에서 entry order·position·보호 OCO를 각각 최대 1개, 전체 대상을 한 symbol로 제한하고 현재 position market value와 남은 entry limit notional 합이 100 USD 이하인지 확인한다. 값이 유효하지 않거나 한도를 넘으면 mutation broker를 열지 않는다.
- cancel과 close가 같은 계획에 있으면 첫 호출은 cancel까지만 실행하고 current-epoch 대사 뒤 반환한다. cancel이 broker terminal 상태로 사라진 다음 호출의 새 close-only 계획에서만 현재 exact 정수 position close를 실행한다.
- 첫 거절·모호 상태에서는 다음 cancel과 close를 모두 중단한다.
- 실행 뒤 같은 운영 세션에서 다시 recovery하고 결과와 복구 상태를 원장에 남긴다. broker 요청 뒤 current-epoch 경계가 바뀌면 일반 사전 차단으로 반환하지 않고 별도 post-mutation reconciliation 오류로 종료한다.
- 성공 보고서는 심볼·조치 종류·mutation 상태만 기록하고 계좌 fingerprint, broker order ID, request ID, mutation key, 자격증명과 원시 payload는 기록하지 않는다. 오류·차단 보고서도 원시 recovery 사유 대신 고정된 안전 범주만 기록한다.

## 고정 smoke 한도

- 최대 notional: 100 USD
- 최대 계획위험: 10 USD
- 최대 동시 포지션: 1
- 일손실 한도: 30 USD
- 편도 비용 가정: 20bp

성과 근거 없이 이 값을 확대하지 않는다.

## 종료코드

- `0`: 조치 없음 또는 모든 계획 조치가 `acknowledged`/`already_acknowledged`
- `1`: broker mutation 전 current-epoch·원장·broker 대사 또는 축소 scope 차단
- `2`: 거절, timeout/응답 모호, cancel 뒤 재대사가 필요한 `incomplete`, 일부 미실행, mutation 뒤 대사 실패 또는 실행 예외

## 검증

- 운영 세션 arm·보호 OCO·안전조치 CLI·executor·scope 표적 회귀: `30 passed`
- 전체 회귀: `594 passed`
- `uv run ruff check .`: 통과
- 변경 Python 15개 파일 `ruff format --check`: 통과
- `uv run basedpyright`: 오류 0, 경고 0
- 수동 CLI QA:
  - `./run_alpaca_paper_safety_mutation_smoke.py --help`: 종료코드 0
  - 잘못된 arm: 자격증명 로딩 전 argparse 종료코드 2
  - fake cancel 단계: 종료코드 2, `incomplete` 보고서 생성
  - fake 재대사 후 close-only 단계: 종료코드 0, `acknowledged` 보고서 생성
  - 보고서에서 fixture secret, account fingerprint, broker order ID와 mutation key 부재 확인

## 실제 Paper 상태

수동 QA 시점은 2026-07-14 18:59 EDT로 정규장이 닫혀 있었다. 실제 CLI를 paper 계정에 연결하지 않았고 이번 체크포인트의 Alpaca Paper POST/DELETE는 0건이다. fake broker와 fixture 기반 E2E는 안전조치 실행·보고 경계만 검증하며 실제 체결 또는 전략 성과의 증거가 아니다.

## 후속 실행 가능성 회귀

README가 직접 실행을 안내하는 `run_alpaca_paper_entry_smoke.py`가 최초 추가 시 Git mode `100644`로 저장된 운영성 결함을 확인했다. 파일 내용과 주문 동작은 바꾸지 않고 mode를 `100755`로 수정했으며, 실제 executable `--help`와 arm 오입력을 자격증명 없이 검증하는 테스트를 추가했다.

- 직접 `--help`: 종료코드 0
- 잘못된 arm: 자격증명 로딩 전 종료코드 2
- entry·보호 OCO·safety smoke 표적 회귀: `19 passed`
- 전체 회귀: `768 passed`
- Ruff: 통과
- basedpyright: 오류 0, 경고 0
- 외부 Alpaca Paper POST/DELETE: 0건

## 다음 안전 게이트

1. 부분체결 수량이 증가할 때 기존 OCO를 cancel하고 늘어난 exact 수량으로 교체하는 current-epoch 상태기계를 구현한다.
2. OCO resize의 cancel/fill race, cancel timeout, replacement POST timeout을 targeted REST·WSS·Account Activities·불변 원장으로 재시작 복구한다.
3. 열린 정규장에서 축소 entry 1건 → 즉시 보호 OCO → armed safety cancel/flatten → open order 0·position 0 최종 대사를 한 번의 smoke 수명주기로 검증한다.
4. 위 게이트 전에는 ORB 반복 Paper POST pilot을 열지 않는다.
