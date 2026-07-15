# ORB 일일 Shadow Trial 운영 체크포인트

날짜: 2026-07-15

상태: **거래일별 preregistration부터 exact terminal evidence까지 watch에 opt-in 연결 완료**

## 목적

ORB forward-validation 한 세션을 global experiment ledger의 독립 `shadow_forward` trial 하나로 보존한다. 장기 adaptive 집계와 별개로 각 날짜가 실제로 시작됐는지, 완전한 evidence로 끝났는지, 데이터 결손으로 검열됐는지, 어느 장후 phase가 실패했는지를 immutable event chain으로 남긴다.

이 trial은 수익 확정, 전략 승격, champion 선언, 주문권한 또는 위험예산 변경이 아니다. lane 간 결과도 섞지 않는다.

## 상태기계

```text
pre-open register
  → regular-session started
  → post-close completed | censored | failed
```

- trial ID는 세션 날짜와 exact strategy version으로 결정론적으로 만든다.
- 신규 registration은 해당 NYSE open 전에만 가능하다. 장중 재실행은 기존 exact registration replay만 허용한다.
- started event는 같은 세션 `[open, close)`에서만 가능하다.
- terminal은 close 이후 sequence 2로만 append하며 이후 event와 terminal 재분류를 금지한다.
- registration은 current manifest·ORB scope·global hypothesis/version·lifecycle·runtime code version을 다시 검증한다.
- prospective data contract와 required/optional artifact 목록의 canonical SHA-256을 registration data version으로 고정한다.

## Terminal Evidence

정상 finalizer는 다음 네 terminal artifact와 그 parent lineage를 다시 검증한다.

1. daily research record 원문 SHA-256
2. adaptive evaluation 원문 SHA-256
3. finalized `LaneDailySnapshot` canonical key
4. exact `LaneReviewEvent` canonical key

모든 daily artifact path·size·SHA-256과 content-bound data version도 다시 계산한다. code, evaluator, feed, parameter, cost, portfolio, scope, parent JSONL, snapshot 또는 review가 preregistered source와 다르면 terminal을 만들지 않는다.

`forward_day_eligible`, daily incidents, snapshot data quality와 snapshot incidents가 모두 완전할 때만 `completed`다. exact evidence는 있으나 품질 문턱을 통과하지 못하면 고정 reason의 `censored`이며 수익 0으로 바꾸지 않는다.

`failed`는 `paper_metrics`, `daily_research_record`, `adaptive_evaluation`, `lane_forward_validation` 중 하나의 같은 세션 post-close audit가 nonzero·failed로 검증될 때만 허용한다. audit가 없거나 성공 행뿐이면 열린 trial을 실패로 꾸미지 않는다.

## CLI와 Watch

`run_orb_forward_trial.py`는 `register`, `start`, `finalize`, `fail` 네 local-only operation만 제공한다. report는 mode 600 atomic file이며 path, trial ID, strategy version, key, hash, raw reason, account·broker 식별자를 노출하지 않는다.

`run_kis_paper_watch.py --experiment-ledger`는 기존 ORB lane forward 네 경로와 함께 지정할 때만 활성화된다.

```text
register → start → KIS scan
→ metrics → daily record → adaptive
→ lane snapshot → Reviewer
→ trial finalize
```

register/start 실패는 첫 provider scan 전에 watch를 중단한다. 장후 child 실패는 해당 audit를 먼저 확정한 뒤 failed terminal child를 호출하고 원래 phase 종료코드를 유지한다. terminal projection 자체가 실패하면 임의 failed event를 만들지 않는다. 옵션이 없으면 기존 watch와 lane runner 동작은 바뀌지 않는다.

watch는 global ledger를 직접 열지 않는다. 짧게 실행되는 child만 trial Writer lease를 소유하며, credential·endpoint·arm·fixture·force 인자를 받거나 전달하지 않는다.

## 검증

- trial service·CLI·watch 통합 focused: 50 passed
- watch trial·기존 lane forward·lane CLI focused: 30 passed
- 전체 회귀: 942 passed
- Ruff lint: 통과
- 변경 Python 파일 Ruff format: 통과
- basedpyright: 0 errors, 0 warnings
- watch executable `--help`: exit 0, `--experiment-ledger` 노출
- unknown option: exit 2
- experiment ledger 단독 partial 설정: exit 2, provider 접근 전 차단
- 장외 완전 설정: exit 0, DB·output 생성 0건
- register/start failure fixture: provider scan 0건
- 성공 fixture: register→start→scan과 장후 finalize 순서 확인
- 네 phase failure fixture: 이후 계산 중단과 exact failed command 확인
- global full-format check는 이번 변경과 무관한 기존 61개 파일의 formatter drift로 nonzero였으며 해당 파일은 수정하지 않음
- Alpaca Paper credential 파일·저장소 `outputs/` 부재 확인
- 실제 Alpaca Paper POST/DELETE: 0건

intraday pilot 한도인 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp와 risk fraction 1/3000은 변경하지 않았다.

## 체크포인트 커밋

- `5673b8d`: ORB 일일 Shadow Trial 설계와 구현 계획
- `72a6745`: pre-open preregistration과 exact lineage 검증
- `4b8777b`: completed/censored terminal evidence와 tamper 차단
- `93f8962`: audited failed terminal과 local-only CLI
- `e811433`: opt-in watch register/start/finalize/fail 스케줄 연결

## 다음 단계

- 열린 정규장·Paper credential·exact current ORB 후보가 모두 있을 때만 축소 entry→보호 OCO→복구→EOD flat smoke 실행
- 실제 적격 세션에서 daily trial을 누적하고 completed/censored/failed와 열린 trial을 운영 대사
- actual partial fill이 자연스럽게 발생할 때만 staged OCO cancel→terminal 대사→replacement 검증
- equal-risk terminal trial과 승격 evidence 계약 뒤 comparison/promotion Controller 구현
- 최소 두 executable lane champion 전 Portfolio Manager 금지

현재 ORB는 확정수익 전략이 아니라 Alpaca Paper forward-validation 후보이다.
