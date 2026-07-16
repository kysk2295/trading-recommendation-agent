# US Swing Shadow Trial And Reviewer 체크포인트

- 날짜: 2026-07-16
- 범위: `us_equities/swing_trading/new_high_momentum`의 source-bound forward shadow evidence
- 상태: fixture E2E와 local-only CLI 검증 완료, 외부 provider/Paper mutation 0건

## 완료된 계약

- `SWING_RESEARCH_CONTRACT`는 `us-swing-new-high-rvol-v1.json`의 hypothesis, falsification rule, single-lane scope와 신고가·RVOL v1 parameter/data/cost/portfolio 계약을 고정한다.
- `LaneId`와 `ExperimentScope` primitive는 import-pure 모듈로 분리했다. research/global ledger와 swing trial/review import closure는 Paper risk, broker, provider, credential, execution 또는 Portfolio Manager를 불러오지 않는다.
- signal 하나는 deterministic trial 하나에만 대응한다. 신규 등록은 `signal_created` 뒤 다음 정규장 open 전만 가능하며, 그 뒤 요청은 정확히 같은 등록의 replay만 가능하다.
- 시작은 trial planned regular session 안에서만 가능하다. 종료는 swing shadow ledger의 `expired`, `stopped`, `targeted`, `time_exit` terminal과 signal/event canonical SHA-256 artifact가 모두 일치할 때만 global `completed`를 append한다.
- Reviewer는 global completed event와 shadow artifact를 query-only로 다시 확인한다. action은 `continue_collection` 하나이고 lifecycle, order authority, allocation 변경 flag는 모두 false다.

## 운영 인터페이스

```text
run_swing_shadow_trial.py register --experiment-ledger ... --shadow-ledger ... --signal-id ... --output-dir ...
run_swing_shadow_trial.py start    --experiment-ledger ... --shadow-ledger ... --signal-id ... --output-dir ...
run_swing_shadow_trial.py finalize --experiment-ledger ... --shadow-ledger ... --signal-id ... --output-dir ...
run_swing_shadow_trial.py review   --experiment-ledger ... --shadow-ledger ... --review-ledger ... --signal-id ... --output-dir ...
```

각 동작은 local SQLite와 redacted mode-600 report만 다룬다. CLI에는 credential, endpoint, arm, force, scheduler, broker mutation 옵션이 없으며 report에는 source path, signal/trial ID, hash, 계좌 정보나 자격증명을 쓰지 않는다.

## 검증

- full project regression: 1673 passed
- CLI `--help`, 없는 원장 register 차단, fixture `register → start → finalize → review`: 수동 QA 완료
- Ruff 전체 검사와 basedpyright 전체 검사: 0 findings

## 다음 단계

현재 NYSE post-close, bounded universe, data credential이 모두 충족될 때만 기존 `run_us_swing_shadow.py`의 read-only source 수집으로 실제 forward signal을 축적한다. 이 checkpoint는 Paper account/order, lifecycle transition, champion, allocation, profitability claim이나 자동 스케줄링을 추가하지 않는다.
