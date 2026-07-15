# Global experiment ledger foundation 체크포인트

날짜: 2026-07-15

상태: **전역 전략 계보·trial 결과·next-session lifecycle을 보존하는 별도 append-only 원장과 intraday bootstrap 구현**

## 목적

lane registry는 lane의 실행정책·account binding·experiment scope·daily snapshot을, review ledger는 독립 Reviewer의 권고를, execution ledger는 Alpaca Paper 주문·체결·복구 상태를 소유한다. 이번 단계는 어느 원장에도 전략의 전역 가설·버전·실험 시도·상태 이력을 섞지 않고 별도 global experiment ledger에 보존한다.

이 체크포인트는 상태 저장과 projection 기반이다. Reviewer 권고를 실제 transition으로 바꾸는 Lifecycle Controller, champion 선언, 주문권한·위험예산 변경과 Portfolio Manager는 구현하지 않는다.

## 저장 계약

schema v1은 다음 다섯 append-only table을 사용한다.

1. `hypotheses`
2. `strategy_versions`
3. `experiment_trials`
4. `experiment_trial_events`
5. `strategy_lifecycle_events`

- DB와 Writer lock은 mode 600이다.
- 한 Writer context가 한 SQLite transaction이며 예외나 immutable conflict가 발생하면 묶음 전체를 rollback한다.
- 두 번째 Writer lease는 비차단 실패한다.
- 모든 table은 UPDATE/DELETE trigger로 변경을 거부한다.
- Reader는 `mode=ro`와 `query_only`를 사용하고 문맥 종료 즉시 connection을 닫는다.
- payload를 다시 parse해 canonical SHA-256 key, identity column, parent hypothesis/version/scope를 검증한다.
- trial event는 sequence 1 `started` 뒤 `completed`·`failed`·`censored` 중 하나로 끝나며 gap·fork·시간 역행·terminal 뒤 append를 거부한다.
- lifecycle event는 exact previous key와 `from_state`를 요구하고 아직 이전 event의 effective date가 오지 않았으면 다음 transition을 거부한다.
- `suspended` 복구는 중단 직전 non-suspended 단계보다 높아질 수 없고 `rejected`는 terminal이다.
- as-of Reader는 요청한 세션 날짜까지 유효한 마지막 lifecycle event만 반환한다.

## 현재 intraday bootstrap

`run_experiment_ledger_bootstrap.py`는 다음 네 인자만 받는다.

```text
--database
--lane-registry
--output-dir
--code-version
```

experiment Writer를 열기 전에 lane registry의 current intraday manifest와 ORB·VWAP reclaim·HOD breakout·Gap-and-Go 네 experiment scope의 payload와 canonical key를 모두 확인한다. 하나라도 없거나 다르면 experiment DB와 Writer lock을 만들지 않는다.

검증이 끝나면 네 hypothesis와 네 strategy version을 등록하고, 각 version을 기록일보다 뒤의 첫 NYSE 정규 세션부터 유효한 `experimental_shadow` registration event 하나로 이관한다. 과거 `idea`·`historical` event를 합성하지 않는다. registration evidence는 exact hypothesis key, scope key, version key 세 개다.

재실행은 최초 sequence-1 기록 시각을 재사용한다. 같은 source와 code version이면 새 행 0건이고, 같은 immutable identity의 내용이 다르면 전체 transaction을 rollback한다. CLI와 서비스는 credential, Alpaca/KIS HTTP, execution store, mutation adapter와 Portfolio Manager를 import하지 않는다.

## 검증

- global ledger 모델·store·bootstrap focused 회귀: 65 passed
- 전체 회귀: 867 passed
- Ruff 전체 저장소: 통과
- 변경 Python 13개 Ruff format: 통과
- basedpyright: 0 errors, 0 warnings
- executable `--help`: exit 0
- unknown option: exit 2, DB·output path 생성 0건
- missing lane source: exit 1, lane DB·experiment DB 생성 0건, redacted blocked report 확인
- fixture canonical source 최초 실행: hypothesis/version/lifecycle 신규 4/4/4
- 같은 source replay: 신규 0/0/0, 기존 기록 시각 유지
- experiment DB mode: 600
- UPDATE/DELETE trigger, second Writer, query-only Reader, exact replay, payload/key corruption, transaction rollback 테스트 통과
- 고정 Alpaca Paper 자격증명 파일: absent
- 저장소 production `outputs/`: absent
- 실제 Alpaca Paper POST/DELETE: 0건

수동 QA와 테스트 report에는 credential, 계좌·fingerprint, broker ID, DB path, canonical key와 raw payload를 기록하지 않았다. intraday pilot 한도인 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp와 risk fraction 1/3000은 변경하지 않았다.

## 체크포인트 커밋

- `fcc83b3`: 공유 전략 연구 계보 상수
- `0bd3e8d`: global experiment 모델과 canonical key
- `a1bf16b`: append-only 등록 원장
- `48a1bf0`: trial/lifecycle chain과 as-of projection
- `c5fb266`: exact lane source 기반 intraday bootstrap CLI

## 여전히 닫힌 범위

- Reviewer evidence를 독립 검증해 lifecycle transition을 append하는 deterministic Controller
- 자동 champion·promotion·demotion과 주문권한 변경
- ORB 일일 forward 결과를 사전등록 trial terminal artifact로 연결하는 운영 loop
- 열린 정규장 최소 entry→보호 OCO→복구→EOD flat 실제 Alpaca Paper smoke
- swing broker 계좌·주문권한과 market-regime 직접 거래권한
- 최소 두 executable lane champion 전 Portfolio Manager와 다음 세션 위험예산 배분

현재 네 전략은 확정수익 전략이 아니라 `experimental_shadow` Paper forward-validation 후보이다.
