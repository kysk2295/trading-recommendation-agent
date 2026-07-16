# 첫 정규장 Alpaca Paper smoke 런북

상태: **실행 절차 확정, 실제 Alpaca Paper POST/DELETE 0건**

이 런북은 실제 자금 거래가 아니라 `intraday_momentum` lane의 첫 Alpaca Paper 기능 smoke만 다룬다. 수익성 표본이나 전략 승격 근거로 사용하지 않는다. 한 단계라도 차단되면 값을 고치거나 새 원장을 만들어 우회하지 않고 그날 mutation을 중단한다.

## 고정 안전 경계

- 거래 endpoint는 코드에 고정된 `https://paper-api.alpaca.markets`만 허용한다.
- entry·보호 OCO·cancel·flatten에는 매 호출 정확한 `ARM_ALPACA_PAPER_ONLY`가 필요하다.
- 최대 notional 100 USD, 최대 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 비용 20bp를 확대하지 않는다.
- 첫 smoke의 source loader는 liquidity 허용량을 1주로 고정한다. CLI에서 늘릴 수 없고 1주가 100 USD 한도를 넘으면 주문은 차단되어야 한다.
- Alpaca Paper CLI를 둘 이상 동시에 실행하지 않는다. execution ledger Writer는 항상 하나다.
- 자격증명 파일을 출력하지 않는다. account fingerprint, broker order ID, request ID 또는 원시 payload도 운영 기록에 복사하지 않는다.
- bootstrap·readiness·recovery·entry·보호 OCO·safety CLI의 report는 shell umask와 무관하게 atomic mode `600`으로 교체된다.
- mutation 결과가 모호하면 동일 POST/DELETE를 다시 보내지 않고 GET-only recovery만 실행한다.
- market close, stale bar, 계좌 변경, 알 수 없는 주문·포지션, 원장 충돌 또는 WSS epoch 불일치는 모두 중단 조건이다.

## 0. 작업 경로와 사전 검증

저장소 루트에서 실행한다.

```bash
cd /Users/goyunseo/work/trading-recommendation-agent
umask 077
export PAPER_DB=outputs/paper_execution/paper_execution.sqlite3
export LANE_DB=outputs/lane_control/lane_registry.sqlite3
export REVIEW_DB=outputs/lane_control/lane_review.sqlite3
export EXPERIMENT_DB=outputs/experiment_control/experiment_ledger.sqlite3
export NY_DATE="$(TZ=America/New_York date +%F)"
export NY_STAMP="$(TZ=America/New_York date +%Y%m%dT%H%M%S)"
export SMOKE_RUN="outputs/paper_execution/smoke/${NY_STAMP}"
export WATCH_RUN="outputs/live_sessions/${NY_DATE//-/}"
export LANE_FORWARD=outputs/lane_control/forward_validation
```

자격증명 값은 읽지 않고 파일 존재와 mode만 확인한다. 두 명령 중 하나라도 nonzero면 여기서 중단한다. 실제 loader가 regular file, 현재 사용자 소유, 비-symlink 조건도 다시 검증한다.

```bash
test -f /Users/goyunseo/.config/trading-agent/alpaca-paper.env
test "$(stat -f '%Lp' /Users/goyunseo/.config/trading-agent/alpaca-paper.env)" = 600
```

코드 기준선을 확인한다. `git status --porcelain`은 빈 출력이어야 한다. global experiment ledger는 parameter-set base와 exact checkout code version으로 결정되는 append-only strategy version을 사용한다. 새 commit이면 아래 local-only bootstrap이 기존 hypothesis를 수정하지 않고 새 code-coupled version을 append한다. 그 bootstrap은 해당 NYSE session open **전**에 끝나야 한다. 정규장 뒤 누락된 preregistration을 새 DB·새 version·시간 변경으로 우회하지 않으며, 그날은 read-only observation만 보존한다. 실패하거나 의도하지 않은 tracked·untracked 변경이 있으면 mutation을 실행하지 않는다.

```bash
git status --short --branch
test -z "$(git status --porcelain)"
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

## 1. GET-only 계좌 결합과 control-plane 결합

빈 Paper 계좌와 새 실행 원장을 최초 한 번 결합한다. 이미 같은 계좌가 결합돼 있으면 exact replay로 성공한다. 기존 주문·포지션, 다른 계좌 또는 기존 intent가 있는 미결합 원장은 차단되어야 한다.

```bash
./run_alpaca_paper_bootstrap.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/01_bootstrap"
```

종료코드 0과 `paper_bootstrap_ko.md`의 `결합: 완료`를 확인한 뒤 같은 execution DB를 intraday lane에 결합한다. 이 단계는 로컬 registry만 쓴다.

```bash
./run_lane_control_plane_bootstrap.py \
  --database "$LANE_DB" \
  --output-dir "$SMOKE_RUN/02_lane_bootstrap" \
  --intraday-execution-database "$PAPER_DB"
```

lane manifest와 scope가 exact current 계약일 때만 global experiment ledger를 bootstrap한다. 이 명령도 로컬 SQLite만 사용하며 주문·자격증명·HTTP·broker 호출이 없다. 현재 clean checkout의 commit으로 code-coupled strategy version을 고정하며, exact replay는 새 행 0건이고 새 commit은 strategy version·lifecycle registration만 append한다.

```bash
./run_experiment_ledger_bootstrap.py \
  --database "$EXPERIMENT_DB" \
  --lane-registry "$LANE_DB" \
  --output-dir "$SMOKE_RUN/03_experiment_bootstrap" \
  --code-version "$(git rev-parse HEAD)"
```

## 2. ORB watch 시작

별도 터미널에서 ORB watch를 시작한다. 네 lane 경로와 global experiment ledger를 함께 지정하면 watch는 provider 호출 전에 해당 NYSE 세션 `shadow_forward` trial을 register하고, 정규장 scan 전에 started event를 append한다. metrics, daily record, adaptive, snapshot, Reviewer가 모두 성공한 뒤에만 장후 terminal을 확정한다. 이 watch는 Alpaca 주문 mutation을 호출하지 않는다.

```bash
./run_kis_paper_watch.py \
  --output-dir "$WATCH_RUN" \
  --cycles 390 \
  --interval-seconds 60 \
  --wait-until-open \
  --strategy orb \
  --top 10 \
  --max-pages 1 \
  --lane-execution-database "$PAPER_DB" \
  --lane-registry "$LANE_DB" \
  --lane-review-ledger "$REVIEW_DB" \
  --lane-forward-output-dir "$LANE_FORWARD" \
  --experiment-ledger "$EXPERIMENT_DB"
```

watch cycle이 실패했거나 `candidate_input_cycles.csv`와 SQLite 후보 입력이 불완전하면 그 cycle의 추천으로 entry를 실행하지 않는다.

## 3. 진입 직전 GET-only 대사

다른 Alpaca Paper CLI가 실행 중이지 않은 터미널에서 순서대로 실행한다.

```bash
./run_alpaca_paper_preflight.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/03_preflight"

./run_alpaca_paper_readiness.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/04_readiness"
```

두 명령 모두 종료코드 0이어야 한다. readiness 성공은 주문 승인이 아니며, 보고서의 broker market open이 `예`인 열린 정규장에서만 다음 단계로 간다. 실제 entry는 새 운영 세션 안에서 WSS 두 Pong, 5초 이내 REST 상태와 원장을 다시 대사한다.

## 4. exact current ORB 후보 감사

production entry source loader는 추천, 추천 생성 당시 후보 입력, 최신 완료 1분봉의 최초 관찰을 같은 watch SQLite의 query-only read transaction에서 결합한다. 현재 30초 이내에 생성됐고 현재 시계 기준 직전 완료 정규장 1분봉을 가리키는 `setup` ORB 후보가 정확히 하나여야 한다. 다음 query는 사람이 계보를 확인하기 위한 선택적 감사 자료이며 mutation 인자를 만들거나 주문을 승인하지 않는다.

```bash
mkdir -p "$SMOKE_RUN/05_candidate"
sqlite3 -readonly -header -csv "$WATCH_RUN/paper_recommendations.sqlite3" > "$SMOKE_RUN/05_candidate/candidate.csv" <<'SQL'
WITH linked AS (
  SELECT
    r.recommendation_id,
    r.symbol,
    r.created_at,
    r.entry,
    r.stop,
    r.target_1r,
    r.target_2r,
    i.latest_completed_bar_at AS bar_start,
    b.first_observed_at AS bar_first_observed,
    i.spread_bps,
    b.volume AS completed_bar_volume,
    (julianday('now') - julianday(r.created_at)) * 86400.0 AS age_seconds
  FROM recommendations AS r
  JOIN candidate_input_snapshots AS i
    ON i.symbol = r.symbol
   AND abs((julianday(i.observed_at) - julianday(r.created_at)) * 86400.0) < 1.0
  JOIN candidate_minute_bars AS b
    ON b.exchange = i.exchange
   AND b.symbol = i.symbol
   AND b.exchange_timestamp = i.latest_completed_bar_at
  WHERE r.strategy = 'opening_range_breakout'
    AND r.state = 'setup'
    AND r.symbol = upper(r.symbol)
    AND r.entry > r.stop
    AND r.stop > 0
    AND i.spread_bps >= 0
    AND b.volume > 0
    AND CAST(strftime('%s', b.exchange_timestamp) AS INTEGER)
        = CAST(strftime('%s', 'now') AS INTEGER)
          - CAST(strftime('%s', 'now') AS INTEGER) % 60 - 60
    AND CAST(strftime('%s', b.first_observed_at) AS INTEGER)
        >= CAST(strftime('%s', b.exchange_timestamp) AS INTEGER) + 60
    AND julianday(b.first_observed_at) <= julianday(r.created_at)
), current_candidate AS (
  SELECT * FROM linked WHERE age_seconds BETWEEN 0 AND 30
)
SELECT
  count(*) OVER () AS eligible_count,
  recommendation_id,
  symbol,
  created_at,
  entry,
  stop,
  target_1r,
  target_2r,
  bar_start,
  bar_first_observed,
  spread_bps,
  completed_bar_volume,
  round(age_seconds, 3) AS age_seconds
FROM current_candidate
ORDER BY created_at, symbol;
SQL
```

감사 query를 실행했다면 `candidate.csv`는 header와 data 1행만 있어야 하고 `eligible_count`는 1이어야 한다. 0건 또는 2건 이상이면 그 cycle에는 entry를 실행하지 않는다. 이 CSV의 값을 다음 명령에 복사하지 않는다. production loader가 recommendation identity, 전체 가격 순서, timestamp instant, 최초 관찰, spread, volume과 정규장 경계를 더 엄격하게 다시 검증하고 recommendation ID를 그대로 intent/client order ID 계보로 사용한다.

## 5. armed entry 1회

아래 명령은 종목·가격·시각·수량을 받지 않는다. source loader는 자격증명이나 WSS/REST 운영 세션을 열기 전에 watch DB에서 단 하나의 현재 ORB 요청을 확정한다. 현재 분이 바뀌거나 후보가 stale·중복·불완전하면 provider mutation 전에 차단된다.

```bash
./run_alpaca_paper_entry_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/06_entry" \
  --watch-database "$WATCH_RUN/paper_recommendations.sqlite3"
```

source 확정 뒤에도 같은 운영 세션의 current-epoch admission이 market clock, NYSE 정규장, 직전 완료 봉, WSS heartbeat, broker/shadow 포트폴리오와 위험 한도를 독립적으로 재검증한다. source loader 성공만으로 주문이 승인되지는 않는다.

- 종료코드 0: entry mutation이 acknowledged 또는 이미 동일하게 acknowledged 됐고 current-epoch 사후 대사가 끝났다.
- 종료코드 1: mutation 전 차단이다. 값을 수정해 우회하거나 새 DB로 재실행하지 않는다.
- 종료코드 2: 거절, 응답 모호, 사후 대사 실패 또는 실행 예외다. 동일 entry를 다시 제출하지 않고 다음 GET-only mutation recovery를 실행한다.

```bash
./run_alpaca_paper_mutation_recovery.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/07_mutation_recovery"
```

첫 smoke에서는 recovery가 `ABSENT`를 증명해도 entry를 재제출하지 않는다. 당일 functional smoke를 중단하고 근거만 보존한다.

## 6. 체결과 보호 OCO

entry ACK 뒤에는 GET-only recovery로 WSS·REST·Account Activities FILL과 원장을 갱신한다. 아직 체결이 없으면 보호 OCO를 추정 생성하지 않는다.

```bash
./run_alpaca_paper_recovery.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/08_fill_recovery"
```

부분 또는 전체 체결이 확인되면 entry 결과 보고서에 기록된 같은 `recommendation_id`로 보호 OCO를 즉시 실행한다. 이 값은 수동 생성하지 않는다.

```bash
./run_alpaca_paper_protective_oco_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/09_protective_oco" \
  --intent-id '<recommendation_id>'
```

exact 포지션을 기존 OCO가 덮으면 noop/0이다. 추가 체결로 보호 수량이 부족하면 첫 호출은 기존 OCO cancel만 실행하고 `incomplete`/2로 끝난다. GET-only recovery에서 cancel terminal을 확인한 다음 새 current epoch의 같은 OCO 명령만 다시 실행해 exact 수량 replacement를 제출한다. cancel과 replacement를 한 호출에서 강제로 이어 붙이지 않는다.

보호 OCO가 거절되거나 timeout으로 모호하면 새 OCO·close·cancel mutation을 겹쳐 보내지 않는다. `run_alpaca_paper_mutation_recovery.py`와 `run_alpaca_paper_recovery.py`만 순차 반복해 targeted 주문 존재 여부를 확정한다.

## 7. cutoff와 EOD 평탄화

15:30 ET 이후 첫 호출은 남은 entry를 취소한다. 15:55 ET 이후에는 entry·보호 OCO cancel을 먼저 확정하고, 다음 current-epoch 호출에서만 현재 exact 정수 포지션을 close한다.

```bash
./run_alpaca_paper_safety_mutation_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/10_safety_mutation"
```

`incomplete`/2는 cancel 뒤 재대사가 필요하다는 정상 staged 상태일 수 있다. 같은 mutation을 즉시 반복하지 말고 먼저 두 GET-only recovery를 실행한다.

```bash
./run_alpaca_paper_mutation_recovery.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/11_eod_mutation_recovery"

./run_alpaca_paper_recovery.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/12_eod_state_recovery"
```

broker cancel이 terminal이고 현재 포지션이 다시 대사된 뒤에만 safety mutation을 다시 실행한다. 최종 호출의 종료코드 0만 완료로 인정한다.

## 8. 최종 flat 대사

마지막 mutation 뒤 GET-only recovery와 preflight를 순서대로 실행한다.

```bash
./run_alpaca_paper_recovery.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/13_final_recovery"

./run_alpaca_paper_preflight.py \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/14_final_preflight"
```

완료 조건은 preflight 종료코드 0, 미체결 주문 0, 열린 포지션 0, unresolved intent 0, broker/shadow/원장 대사 통과다. 하나라도 다르면 smoke는 실패이며 다음 날 새 entry를 금지한다.

## 9. 장후 lane snapshot·Reviewer·trial terminal

watch가 정상 종료하면 metrics→daily record→adaptive→lane snapshot→Reviewer→trial terminal이 자동으로 한 번 실행된다. `post_session_orb_trial_terminal_cycles.csv`가 0이고 local trial report가 completed 또는 censored인 경우에만 일일 trial이 닫힌다. `completed`는 수익 확정이나 승격 근거가 아니며 `censored`는 수익 0이 아니다.

자동 lane 단계가 실행되지 않았지만, 이 런북의 같은 preregistered trial·최종 flat 대사·장후 source가 이미 준비된 경우에는 다음 GET/WSS-only runner를 한 번 실행할 수 있다.

```bash
./run_orb_lane_forward_validation.py "$WATCH_RUN" \
  --session-date "$NY_DATE" \
  --execution-database "$PAPER_DB" \
  --lane-registry "$LANE_DB" \
  --review-ledger "$REVIEW_DB" \
  --output-dir "$LANE_FORWARD"
```

snapshot과 Reviewer가 모두 성공한 경우에만 같은 exact trial을 local-only finalizer로 닫는다.

```bash
./run_orb_forward_trial.py finalize "$WATCH_RUN" \
  --experiment-ledger "$EXPERIMENT_DB" \
  --lane-registry "$LANE_DB" \
  --review-ledger "$REVIEW_DB" \
  --session-date "$NY_DATE" \
  --output-dir "$LANE_FORWARD/trials/$NY_DATE/finalize"
```

manual runner 또는 finalizer가 nonzero이면 다른 terminal kind를 추정하거나 임의 audit로 `failed`를 만들지 않는다. 자동 watch가 phase 종료코드를 먼저 audit한 경우에만 그 audit로 failed terminal을 시도한다. 그렇지 않은 수동 실패는 열린 trial과 source를 보존해 reconciliation 대상으로 남긴다. snapshot success 뒤에만 Reviewer가 실행되며 Reviewer는 권고만 append한다. 첫 smoke 결과는 기능 검증 근거일 뿐 확정수익이나 승격 근거가 아니다.

## 즉시 중단 조건

- fixed credential 파일 부재, mode·owner·regular-file 조건 실패
- live endpoint 흔적 또는 Paper account fingerprint 변경
- bootstrap, preflight, readiness, recovery의 nonzero 종료
- exact current ORB 후보가 1건이 아니거나 candidate age가 30초 초과
- 현재 분 rollover 뒤 watch DB의 stale 후보를 계속 사용하려는 경우
- Writer lease 충돌, 알 수 없는 주문·포지션, immutable 원장 충돌
- entry·OCO·cancel·close mutation의 거절·timeout·모호 상태
- 보호 OCO보다 broker 포지션 수량이 큰 상태
- 15:55 ET 이후 open order·position이 남거나 최종 preflight가 nonzero
- watch/daily/adaptive/lane snapshot/Reviewer source 무결성 실패
- global experiment ledger bootstrap·trial register/start/terminal audit nonzero 또는 현재 checkout code version 불일치

중단 시 실제 mutation을 더 만들지 않고 보고서와 append-only 원장을 보존한다. 자격증명, account fingerprint, broker ID 또는 원시 API payload는 이슈·커밋·채팅에 붙이지 않는다.
