# 여섯 독립 전략 연구 에이전트 설계

**작성일:** 2026-08-19
**상태:** 구현된 독립 연구 경로의 현재 계약. 2026-08-17 설계는 보존하되, 이 문서가 이후 구현 결정을 명시적으로 supersede한다.
**범위:** 연구·paper-only 실행 경계. 수익성이나 실거래를 보장하지 않는다.

## 1. 현재와 목표

| 구간 | 현재 구현(근거 파일) | 검증된 경계 |
|---|---|---|
| Protocol | `strategy_research_models.py`, `strategy_research_catalog.py`: source-bound immutable hypothesis/preregistration hash와 6개 identity/cadence | hypothesis가 order authority나 profitability claim을 가질 수 없음 |
| Kernel | `strategy_research_science_kernel.py`: per-owner deterministic kernel, all-attempt record, one-time sealed holdout, sanitized feedback | fixture vertical은 wiring-only이며 live research result를 대체하지 않음 |
| Runtime | `strategy_research_runtime.py`, `strategy_research_runtime_state.py`: agent별 cursor, open work, lease/recovery, due selection | source 누락·실패가 다른 owner를 block하지 않으며 tick당 heavy cycle은 최대 하나 |
| Ledger | V9 `experiment_ledger_schema.py`, `strategy_research_ledger.py`: preregistration, attempts, state event, seal/reveal append-only schema | second holdout reveal과 payload conflict는 거부 |
| OS/feedback | `research_os_runtime.py`, `strategy_research_feedback_runtime.py`: private work queue, owner-safe reinjection, 30초 production run | legacy StrategyLab bundle은 production path에서 읽지 않음 |
| NYSE close | `strategy_research_close_report.py`: persisted state에서 six-owner `DAILY_SUMMARY` Hermes projection | replay는 idempotent, report는 research-only/order authority false |

`strategy_lab_*`의 synchronized trace와 fixture는 호환성·diagnostic 증거로만 남아 있다. 그것은
독립 runtime, current production intake, holdout 평가 또는 수익성 evidence가 아니다.

## 2. 여섯 독립 agent 계약

각 행은 서로 다른 `agent_id`, cursor, search-family budget, hypothesis lineage, cadence를 가진다. 공통 서비스는 읽기 전용으로 호출한다.

| agent_id / 정체성 | 방법론·산출물 | cadence (기준 시각) |
|---|---|---|
| `intraday_momentum` / 장중 추세 | 최신 완료 bar의 돌파·추세 지속, 비용 포함 intraday protocol | 거래일 5분 bar close 후 5분 |
| `intraday_mean_reversion` / 장중 평균회귀 | spread/잔차의 과대이탈과 복귀, same-bar 충돌은 stop | 거래일 5분 bar close 후 5분 |
| `catalyst_event` / 촉매 이벤트 | point-in-time 공시·뉴스 event window, 사전 정의 surprise | 이벤트 receipt 도착 후 15분(세션 외 대기) |
| `swing_trend_regime` / 스윙·레짐 | 일봉 trend와 regime 조건, 다음 세션부터 forward label | 세션 종료 30분 후 1일 1회 |
| `cross_sectional_quant` / 횡단면 정량 | 동일 timestamp universe ranking, sector/turnover neutral baseline | 세션 종료 45분 후 1일 1회 |
| `derivatives_volatility` / 파생·변동성 | IV/term-structure/skew와 현물 outcome의 교차 검증 | 옵션 close 확정 후 1일 1회 |

Agent는 아이디어와 evidence ref만 제안하며 holdout 값, promotion, 주문 권한은 갖지 않는다.

## 3. 공유 evidence 서비스

* `OpportunityEvidenceService`: provider receipt, source hash, point-in-time `as_of`, universe snapshot, 관측식, coverage/staleness, novelty/중복 링크를 반환한다. 쓰기는 immutable source/card 등록뿐이다.
* `MarketContextEvidenceService`: 세션·레짐·spread·유동성·macro/volatility context를 동일 timestamp로 조인하고 revision policy와 missing reason을 반환한다.
* 두 서비스 모두 `EvidenceRef {evidence_id, source_id, as_of, available_at, payload_sha256}`만 노출한다. 현재 세션 최신 완료 bar가 아니거나 stale/missing이면 live recommendation을 만들지 않는다.

## 4. Immutable Hypothesis V2 (전체 필드)

다음 필드는 생성 순간 canonical JSON으로 hash하며 update/delete하지 않는다.

`hypothesis_id`, `parent_hypothesis_id`, `search_family_id`, `agent_id`, `owner_family`, `lane_id`, `created_at`, `created_by`, `source_refs[]`, `evidence_hashes[]`, `point_in_time_policy`, `universe_definition`, `universe_snapshot_id`, `instrument_scope`, `predictor_formula`, `sampling_timestamp`, `target_formula`, `target_horizon`, `expected_direction`, `entry_rule`, `exit_rule`, `stop_rule`, `invalidation_rule`, `economic_mechanism`, `alternative_explanations[]`, `counterfactual_baseline`, `baseline_id`, `cost_model_id`, `slippage_model_id`, `primary_metric`, `secondary_metrics[]`, `falsification_rule`, `free_parameters[]`, `search_budget`, `minimum_observations`, `power_or_ci_gate`, `multiple_testing_family`, `max_attempts`, `train_period`, `validation_period`, `holdout_period_sealed_ref`, `holdout_access_policy`, `model_hash`, `prompt_hash`, `protocol_version`, `code_sha256`, `data_manifest_sha256`, `status`, `trading_authority=false`, `profitability_claim=false`.

Critic은 field 누락, source constructibility, timestamp 순서, 방향·target 불일치, budget 초과, duplicate lineage를 거부한다. 사전등록 뒤 metric/threshold/기간을 바꾸려면 새 `hypothesis_id`다.

## 5. 결정론적 Science Kernel lifecycle

`OBSERVED → DRAFTED → {CRITIC_REJECTED | PREREGISTERED}`; `PREREGISTERED → EXPLORING → PRE_HOLDOUT_REVIEW → HOLDOUT_EVALUATED → {SUPPORTED | REFUTED | INCONCLUSIVE}`; only `SUPPORTED → FORWARD_SHADOW → {PAPER_CANDIDATE | CLOSED}`, while `REFUTED` and `INCONCLUSIVE` go directly to `CLOSED`. Shadow information sufficiency may create `PAPER_CANDIDATE` only with `owner_approval_required=true`; it never automatically promotes a strategy or grants order authority.

각 전이는 입력 hash, actor, aware timestamp, reason code를 검증하고 append-only event를 쓴다. `EXPLORING`은 train/validation과 고정 search budget만 사용한다. `HOLDOUT_EVALUATED`는 sealed holdout을 lineage당 정확히 한 번 읽고 terminal 결과를 만든다. 같은-bar stop/target은 stop이며, 정보 날짜·세션이 현재가 아니면 recommendation을 차단한다.

## 6. Attempts, holdout, feedback firewall

`attempt_id`, parent hypothesis, branch index, input hashes, code/data manifest, started/finished time, status(`started|succeeded|failed|aborted|timed_out|cancelled|censored`), artifact refs, error class, resource limits를 매 시도 append한다. 실패·중단 branch도 multiple-testing family 분모에 남긴다.

Holdout은 `seal_id`, data snapshot/hash, sealed_at, owner, access counter를 갖고 한 번만 공개한다(`access_counter=1` 원자 검증). generator/owner는 holdout의 정확한 metric·구간·종목 기여를 보지 못한다. Reviewer의 구조적 실패 유형만 다음 draft에 reinject하고, holdout 패턴·수치는 firewall에서 제거한다. 새 feature/설명은 새 lineage와 budget이다.

## 7. 독립 cursor, open work, recovery, 결과 reinjection

agent별 `cursor {last_event_id, last_available_at, version}`, `open_work {hypothesis_id, attempt_id, state, lease_until}`, `recovery {checkpoint_hash, retry_count, next_retry_at, reason}`를 원장에 append한다. 한 agent가 실패해도 다른 agent cursor는 전진하며, lease 만료 시 같은 immutable input을 idempotency key로 재개한다. owner 결과는 `owner_agent_id`, `result_event_id`, `outcome`, `reason_codes`, `artifact_refs`로만 reinject하고 holdout 값은 제외한다. 다음 wake는 해당 owner cadence와 evidence `available_at`의 max이다.

## 8. Hermes close-report (정확한 필드)

`source_event_id`(unique), `root_source_event_id`(nullable), `kind=DAILY_SUMMARY`, `market_id`, `agent_family`, `lane_id`(nullable), `strategy_version`(nullable), `instrument_id`(nullable), `occurred_at`(aware), `status`, `evidence_refs[]`, `rendered_text`, `payload_sha256`를 모두 기록한다. 하나의 six-owner report는 날짜당 하나의 `source_event_id=strategy-research-close-report:<session-date>`를 사용하며, 같은 session의 replay는 insert하지 않는다. text에는 `Research-only; profitability claim: false; order authority: false`를 포함한다.

## 9. 안전·검증 경계

Fixture/synthetic/replay/backtest는 schema·누수·상태기계 검증에만 사용하며 profitability evidence나 promotion 근거가 아니다. KIS·LS·기타 provider는 read-only; Alpaca는 정확히 `https://paper-api.alpaca.markets`만 허용하고 endpoint guard와 risk kernel 통과 뒤 paper order/cancel/flatten만 허용한다. live URL·credential·실거래 경로는 금지한다.

## 10. 현재 acceptance와 남은 운영 검증

현재 코드와 focused tests가 증명한 acceptance:

- [x] 여섯 agent는 서로 다른 cursor·family·cadence로 restart 후에도 duplicate attempt 없이 전진한다.
- [x] source/as-of가 검증된 immutable hypothesis와 preregistration hash를 기록한다.
- [x] successful·failed·aborted 등 모든 attempt와 recovery state가 V9 ledger에 append되며, holdout 두 번째 접근은 원자적으로 거부된다.
- [x] holdout 정확값은 generator/owner feedback에 나타나지 않는다.
- [x] 동일 canonical input은 deterministic `SUPPORTED`/`REFUTED`/`INCONCLUSIVE` terminal result를 낸다.
- [x] persisted state는 six-owner Hermes close report로 projection되고 same-session replay는 idempotent다.
- [x] fixture/synthetic/replay 결과는 wiring-only이고 profitability claim·order authority를 만들지 않는다.
- [x] live Alpaca URL 및 KIS/LS mutation endpoint는 HTTP 이전에 거부되고 Alpaca는 paper-only guard/risk kernel을 통과해야 한다.

아직 주장하지 않는 운영 결과:

- [ ] 실제 시장 source의 지속적인 all-six cadence, long-horizon OOS sample, 또는 profit evidence.
- [ ] fixture/matrix/legacy StrategyLab 결과의 promotion, allocation, order, profitability 사용.
- [ ] Alpaca Paper의 실거래 또는 non-Alpaca provider mutation.

CLI help, malformed input, fixture/source-bound vertical, busy lease, restart cursor 및 close projection은
각 릴리스에서 artifact로 재검증한다. 이 checklist는 현재 행동만 나타내며, legacy lockstep을
독립 agent 구현으로 다시 해석하지 않는다.
