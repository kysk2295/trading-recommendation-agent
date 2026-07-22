# M6 Intraday 자동 연구·심사 수직축 체크포인트

날짜: 2026-07-22

## 판정

현재 저장소의 intraday challenger family 하나를 대상으로 다음 local-only 수직축을 연결했다.

```text
명시적 hypothesis bundle
→ canonical intraday contract·strategy version 사전등록
→ bounded historical replay
→ 비용·슬리피지 포함 순차 OOS fold
→ query-only Independent Reviewer
→ content-addressed append-only evidence
```

새 데이터 플랫폼이나 broker adapter를 만들지 않았다. 기존 lane registry, global experiment ledger,
intraday strategy contract, scanner·engine·metrics kernel과 private immutable-file publication을 재사용한다.

## 가설과 실험 경계

`examples/research/intraday-challenger-bundle-v1.json`은 다음 exact canonical hypothesis를 결박한다.

- `H-MOM-VWAP-001` / `vwap_reclaim`
- `H-MOM-HOD-001` / `hod_breakout`
- `H-MOM-GAP-001` / `gap_and_go`

bundle의 hypothesis ID가 현재 `strategy_contract()`와 다르면 experiment ledger를 만들기 전에 차단한다.
각 trial은 `historical_replay`, evaluator version, input CSV SHA-256, manifest SHA-256, 최대 bar·session,
비용, bootstrap 횟수와 RSS budget을 시작 전에 고정한다. 세 전략은 한 비차단 empirical lease 아래 순서대로
실행하므로 프로세스 간 무거운 실험은 동시에 하나만 열린다. 입력은 최대 100,000 bars·60 sessions로
제한되고 `regend_us_stocks` 전체-universe 경로는 명시적으로 거부한다.

현재 전략 parameter는 이미 immutable contract에 고정돼 있어 training fold에서 재최적화하지 않는다.
선언된 minimum training session 뒤의 각 거래일만 독립 engine fold로 평가하고, 편도 fee+slippage 합계
20~100bp를 기존 metrics kernel에 적용한다. 각 fold 직전과 전체 완료 직후 RSS가 bundle 상한 미만인지
확인하며 manifest 상한은 9.5 GiB를 넘을 수 없다.

## 독립 Reviewer와 evidence

Reviewer는 query-only `ExperimentLedgerReader`와 immutable experiment artifact만 받는다. exact trial,
strategy/evaluator/data version, `started → completed` event chain과 artifact SHA-256이 모두 맞아야 심사한다.

- `promote`: 최소 20 OOS sessions·30 trades, cost-adjusted PF 1.15 이상, 평균수익 양수, bootstrap CI 하한 0 이상
- `demote`: 최소 5 OOS sessions·10 trades, PF 0.75 미만, 평균수익 음수, bootstrap CI 상한 0 미만
- `hold`: 위 두 gate가 확정되지 않은 모든 경우

결과는 experiment와 review 각각 content-addressed JSON으로 append-only 게시되고 mode 600으로 다시 읽어
검증한다. 권고 payload의 lifecycle, allocation, order-authority 변경 flag는 모두 `false`다. 즉 `promote`도
자동 승격 명령이 아니며 별도 lifecycle policy와 forward evidence 없이는 상태를 바꾸지 않는다.

## CLI

```bash
uv run python run_lane_control_plane_bootstrap.py \
  --database outputs/research/m6/lane.sqlite3 \
  --output-dir outputs/research/m6/lane-report

uv run python run_intraday_research_loop.py \
  --manifest examples/research/intraday-challenger-bundle-v1.json \
  --input-csv examples/example_intraday.csv \
  --lane-registry outputs/research/m6/lane.sqlite3 \
  --experiment-ledger outputs/research/m6/experiment.sqlite3 \
  --artifact-root outputs/research/m6/artifacts \
  --review-root outputs/research/m6/reviews \
  --output-dir outputs/research/m6/report
```

private artifact 경로는 기존 no-symlink identity 계약을 따른다. macOS 임시 경로는 `/var/...` 별칭이 아니라
물리 경로 `/private/var/...`를 사용해야 한다.

## 검증 증거

- focused CLI·bounded replay·walk-forward·Reviewer: `12 passed`
- full repository suite: `3304 passed in 190.03s`, maximum RSS `382,828,544 bytes`
- actual CLI help: exit `0`
- invalid manifest: exit `1`, blocked report, experiment ledger 미생성
- actual happy path와 exact replay: exit `0/0`
- replay 뒤 experiment trials `3`, terminal event `6`, experiment artifact `3`, review artifact `3`
- current repository fixture Reviewer: `hold, hold, hold`
- replay report 신규 experiment/review artifact: `0/0`
- artifact·review file mode: `600`
- actual happy-path maximum RSS: `47,759,360 bytes`
- changed Python no-excuse audit: `0 violations`

## 남은 경계

현재 repository fixture는 단일 session이므로 세 `hold` 결과는 CLI 수직 연결 증거일 뿐 전략 성과나 승격
증거가 아니다. point-in-time multi-session history, purge/embargo가 필요한 parameter search, regime·liquidity
cohort, DSR/PBO·parameter plateau, shadow forward와 broker Paper 대사는 이후 별도 evidence contract다.
이 체크포인트에서 credential read, provider request, broker/account/order POST, launchd 변경과 실제 프로세스
중단은 모두 0건이다.
