# US News-Catalyst Day Session Scheduler 체크포인트

## 범위

미국 뉴스 촉매 shadow-forward 연구의 장전 등록부터 장후 독립 Reviewer까지를 재시작 가능한 일일 control loop로 연결했다.

```text
register -> start -> collect -> observe -> finalize -> review
```

이 scheduler는 연구 산출물만 운영한다. 뉴스 방향, 진입가, 포지션 크기, lifecycle 승격, 계좌 조회 또는 주문 권한이 없다. Alpaca 사용 범위도 cohort feature를 위한 market-data GET-only다.

## Immutable Session

`run_us_news_catalyst_day_session.py init`은 research strategy version, code version, NYSE session date와 모든 domain store/root를 mode-600 immutable manifest에 결박한다. projection과 evidence는 장중에 생성되므로 manifest는 두 immutable artifact root를 미리 고정하고, `start` 시점에 다음 조건을 만족하는 최신 projection 하나만 선택한다.

- exact strategy version과 session
- `ranked` terminal projection
- projection 시각 이하의 현재시각
- 아직 만료되지 않은 5분 Opportunity
- projection의 exact evidence bundle ID와 파일

여러 scheduler process가 같은 session을 열면 audit store의 nonblocking file lease를 먼저 획득한 하나만 tick을 진행한다. lease는 child command와 사후 domain 검증이 끝날 때까지 유지된다.

## Domain-First Recovery

phase 완료 여부는 scheduler가 저장한 상태 flag가 아니라 다음 실제 산출물에서 다시 계산한다.

| Phase | 완료 근거 |
|---|---|
| `register` | global experiment ledger의 exact daily trial registration |
| `start` | sequence-1 `STARTED` event와 exact frozen cohort |
| `collect` | cohort-bound plan, 전체 feature artifact와 collection receipt |
| `observe` | exact trial/cohort와 전체 symbol을 결박한 setup manifest |
| `finalize` | terminal event와 content-bound outcome artifact |
| `review` | strategy/session과 current trial을 포함한 Reviewer artifact |

한 tick은 앞선 근거를 모두 검증하고 아직 확정되지 않은 phase 하나만 처리한다. child가 domain commit을 마친 뒤 scheduler audit append 전에 프로세스가 죽으면 다음 tick은 현재 content hash를 발견해 child를 다시 실행하지 않고 `recovered` event를 append한다.

audit event는 session별 sequence와 previous event ID를 가진 hash chain이다. SQLite UPDATE/DELETE trigger, exact schema, mode `600`, current-user regular file, single hard-link와 payload SHA를 매 read에서 검증한다. 동일 evidence hash를 attestation한 event만 해당 phase의 audit 완료로 인정한다.

## Time And Failure Policy

- `register`: NYSE open 전만 신규 실행
- `start`: 정규장 안의 유효 projection에서만 실행
- `collect`: cohort 관측 `+30분`을 엄격히 초과한 뒤 `+32분`까지
- `observe`: 같은 causal window 안의 2분 이내 complete feature cycle만 사용
- `finalize`: setup horizon 이후 실행
- `review`: NYSE close 이후 실행

정확한 `+30:00`에는 마지막 bar의 end가 현재시각과 같으므로 수집하지 않고 waiting한다. `+30:01`부터 그 bar는 현재시점에 완료된 근거가 된다. model, scope validator와 scheduler action이 같은 strict lower bound를 사용한다.

READY cohort가 collection/observation 창을 놓치면 성공 또는 0성과로 바꾸지 않는다. 각각 deterministic `collection_window_missed`, `observation_window_missed` skipped evidence를 남기고, terminal은 `missing_setup_observations` censored outcome으로 확정한다. 장 마감 뒤 프로세스가 복구돼도 session close부터 최대 24시간 안에는 terminal을 append할 수 있어 Reviewer loop가 영구 정지하지 않는다.

## CLI

```bash
uv run python run_us_news_catalyst_day_session.py init \
  --registration-manifest examples/us_news_catalyst/research-registration.json \
  --session-date YYYY-MM-DD \
  --experiment-ledger <experiment-ledger.sqlite3> \
  --projection-root <immutable-projections> \
  --evidence-root <immutable-news-evidence> \
  --security-master-store <alpaca-security-master.sqlite3> \
  --session-root <private-session-root> \
  --manifest <private-session-manifest.json> \
  --output-dir <private-report-root>

uv run python run_us_news_catalyst_day_session.py tick \
  --manifest <private-session-manifest.json> \
  --output-dir <private-report-root>
```

`tick`은 cron/launchd가 짧은 간격으로 호출하는 one-shot 명령이다. waiting, completed, recovered, skipped와 모든 phase 완료는 exit `0`; invalid evidence, missed register/start window, child failure 뒤 domain evidence 부재와 lease 충돌은 redacted exit `1`이다.

## 검증

- focused scheduler/collector/trial 회귀: `30 passed`
- six-phase fixture E2E: 6 child commands, 최초 `84 GET`, 4 feature, receipt, setup, terminal, review
- 완료 replay: 추가 child command `0`, 추가 provider GET `0`
- exact `+30:00`: waiting; `+30:01`: causal collection 성공
- post-close missing observation: censored terminal 복구 성공
- full suite: `3126 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- changed-file no-excuse audit: 위반 `0`
- actual CLI help: exit `0`
- missing manifest: exit `1`, mode-600 blocked report, 입력 경로 비노출
- actual init/recovery/waiting: exit `0/0/0`, audit `register/recovered`
- manifest, audit DB와 report: mode `600`
- manual QA provider·credential·account·order operation: `0`

## 남은 운영 경계

fixture E2E와 control-plane recovery는 검증됐지만 production Alpaca News/SIP 정규장 tick, launchd 등록, 장기 soak와 실제 forward sample은 아직 없다. 다음 체크포인트는 pre-open manifest 생성과 30초 one-shot tick을 Mac mini launchd에 read-only/shadow service로 배치하고, 첫 production 정규장에서는 명시된 bounded GET-only cohort 한 건을 관찰하는 것이다.

Reviewer의 `comparison_ready`도 lifecycle 승격이나 주문권한을 자동 변경하지 않는다. 최소 20 clean session과 arm별 100 observations를 채운 뒤 별도 승인된 lifecycle milestone에서만 비교 근거로 사용할 수 있다.
