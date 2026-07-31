# Output-first Research Agent 벤치마크 조사

- 조사일: 2026-07-31
- 조사 범위: LEAN, NautilusTrader, Qlib, Qlib OnlineManager, RD-Agent,
  OpenBB, ArcticDB, FinNLP
- 확장 조사: FinRobot, FinRL, FinRL-X
- 조사 원칙: 공식 문서와 공식 저장소의 2026-07-31 시점 최신 default branch만 사용

## 1. 결론

기존 기획에 나열된 벤치마크는 모두 같은 종류의 제품이 아니다.

- **연구 폐루프:** RD-Agent와 Qlib
- **replay/live 실행과 결과 증거:** LEAN과 NautilusTrader
- **사용자가 읽는 결과 표면:** OpenBB, 확장 비교의 FinRobot
- **데이터 재현성:** ArcticDB
- **source coverage:** FinNLP
- **실험 파일·차트:** 확장 비교의 FinRL과 FinRL-X

따라서 어느 한 제품의 프레임워크를 가져오는 방식은 맞지 않는다. 현재 프로젝트에 이미 있는
provider, experiment ledger, replay, Paper/shadow, Hermes를 유지하고, 그 위에서 각 agent가
`가설 → 도구 실행 → 결과 artifact → 평가 → 다음 행동`을 실제로 닫아야 한다. 매 cycle의
결과는 machine-readable record뿐 아니라 사용자가 읽는 narrative, table, 선택적 chart로 보여야
한다.

## 2. 현재 프로젝트와 기획 의도의 차이

2026-07-31 로컬 권위 store와 autonomous receipt를 읽어 확인한 상태다.

| 확인 항목 | 실제 관측 | 판단 |
|---|---|---|
| Autonomous Research receipts | 26건 모두 `approved_schedule`; `new_data`, `market_event`, `experiment_result`, `reviewer_feedback` 0건 | 상시 연구가 아니라 release QA 실행 흔적이다. |
| Autonomous result | 6건 중 5건 failed. 나머지 1건은 evidence 0건이고 본문이 “Blocked before source-bound research could start”인데 state는 completed | agent 결과가 아니라 실행 상태 오류다. |
| Experiment ledger | hypothesis 4, research source 0, research hypothesis card 0, trial 2 | 가설 seed와 trial 등록은 있으나 source-backed 연구가 닫히지 않았다. |
| Trial events | 2건 모두 `started`, terminal event 0 | 완료된 실험 결과와 feedback이 없다. |
| Lane Reviewer | event 1건 | 반복 연구 feedback loop로 볼 수 없다. |
| Hermes delivery | 67건: Opportunity watch 56, Opportunity incident 4, Day no-recommendation 4, Day daily-summary 2, Day incident 1 | Context, Swing, Systematic, Derivatives의 실제 연구 결과가 없다. |
| 일일 연구 요약 | 2026-07-30 완료 shadow 거래 0, data quality incomplete | 운영 요약은 있으나 새 전략 발견·실험 결과가 아니다. |

즉 코드에는 가설 모델, trial 원장, isolated model 실행기, replay와 Hermes delivery가 이미 있다.
문제는 인프라 부재가 아니다. 실제 evidence trigger가 여섯 agent의 연구 판단을 깨우고, 도구를
실행하고, terminal experiment/recommendation과 다음 연구로 이어지는 제품 폐루프가 관측되지
않는 것이다.

## 3. 벤치마크별 실제 산출물과 채택 판단

| 벤치마크 | 공식 구현에서 확인한 산출물 | 채택 | 수정 | 거부 |
|---|---|---|---|---|
| RD-Agent | hypothesis, task, workspace code, 실행 feedback, metric JSON, chart HTML, next hypothesis, trace | hypothesis→implementation→execution→feedback lineage | six agent mission과 기존 ledger에 맞춘 single-parent append history | generic multi-agent runtime, demo를 production 증거로 간주 |
| Qlib | experiment/recorder state, params, metrics, model/object artifact, prediction, IC/ICIR, backtest/risk analysis | experiment recorder와 terminal artifact 규칙 | 기존 experiment ledger에 metric/table/chart ref 추가 | Qlib 전체 workflow 교체, online order 지원 가정 |
| LEAN | backtest/live job, event callbacks, order/fill/result handler | 동일 strategy version의 replay/Paper 결과 비교 | 현재 deterministic tool과 Paper 경계에 연결 | 엔진 교체, backtest/live 완전 동일성 가정 |
| NautilusTrader | event replay, order/fill/position/account report DataFrame | market event→strategy→execution 결과와 structured report | 최신 bar/session 및 Paper/shadow 결과에 맞춤 | Rust/async runtime 도입, bar replay를 실거래 현실성으로 간주 |
| OpenBB | `results`, `provider`, `warnings`, `chart`, `extra`; DataFrame; text/table/chart widget | 사용자-facing result envelope | research card/report에 evidence, query, next action 추가 | OpenBB extension/CLI/FastAPI/hosted agent stack 도입 |
| ArcticDB | snapshot/as-of, resolved version, metadata, write timestamp | input snapshot identity와 as-of 재현성 | 기존 hash/version field로 표현 | 새 DB migration; 이것을 연구 결과로 간주 |
| FinNLP | 뉴스·공시 downloader의 DataFrame | Opportunity/Context source coverage 참고 | 기존 provider adapter에 필요한 source만 선택 | persistent agent 또는 research loop benchmark로 사용 |
| FinRobot (확장) | evidence-linked 13-chapter report, numeric provenance, HTML/PDF, charts, Bull/Bear/Judge synthesis | output-first report와 evidence/numeric 분리 | request-driven pipeline을 persistent six actor 결과로 단순화 | 전체 platform/frontend, scheduler 내부 구조 |
| FinRL (확장) | train/trade CSV, trained model, metrics, backtest PNG | dataset→experiment→artifact 묶음 | bounded experiment result로 사용 | autonomous agent로 간주, 수익성 증거로 간주 |
| FinRL-X (확장) | reproducible backtest/Paper command, chart/table | replay/Paper output 비교 형식 | 기존 execution vertical을 유지 | trading platform 교체, README 수익률 주장 채택 |

## 4. 공식 근거

### 4.1 RD-Agent와 Qlib: 실제 연구 폐루프

RD-Agent는 연구 절차를 hypothesis, experiment design, code implementation, execution,
metrics/loss feedback, next iteration으로 정의한다. 구현의 `Hypothesis`,
`ExperimentFeedback`, `HypothesisFeedback`, `Trace`는 이유·관찰·코드 변경·실행 예외·다음
가설을 보존한다.

- [RD-Agent 연구 loop](https://github.com/microsoft/RD-Agent/blob/4f9ecb005881cddc08df0124a2e894c018007679/README.md#L488-L492)
- [RD-Agent hypothesis/feedback/trace 모델](https://github.com/microsoft/RD-Agent/blob/4f9ecb005881cddc08df0124a2e894c018007679/rdagent/core/proposal.py#L24-L176)
- [RD-Agent hypothesis/task/code/feedback/chart/metric wire format](https://github.com/microsoft/RD-Agent/blob/4f9ecb005881cddc08df0124a2e894c018007679/rdagent/log/server/README.md#L55-L240)
- [RD-Agent demo와 금융 사용 한계](https://github.com/microsoft/RD-Agent/blob/4f9ecb005881cddc08df0124a2e894c018007679/README.md#L430-L435)

Qlib은 workflow가 만든 정보와 artifact를 recorder에 저장하며 prediction, signal analysis,
IC/ICIR, backtest와 risk analysis를 record template으로 만든다. 반면 공식 online 문서는
public-data 흐름에서 next-day order generation을 지원하지 않는다고 명시한다.

- [Qlib workflow artifact](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/docs/component/workflow.rst#L11-L28)
- [Qlib recorder와 record template](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/docs/component/recorder.rst#L42-L148)
- [Qlib recorder 상태와 artifact API](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/workflow/recorder.py#L28-L155)
- [Qlib OnlineManager 한계](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/docs/component/online.rst#L16-L28)

### 4.2 LEAN과 NautilusTrader: 실행·replay·report 증거

LEAN은 한 engine job에서 live/backtest synchronizer와 result handler를 선택한다. 공식 문서는
backtest order callback은 synchronous지만 live brokerage order event는 asynchronous라고
구분한다. 그러므로 code path 공유는 채택하되 결과 동일성은 가정하면 안 된다.

- [LEAN engine job과 synchronizer](https://github.com/QuantConnect/Lean/blob/962fcd6b58a56d7a52cf7178a42b965ff3681115/Engine/Engine.cs#L43-L114)
- [LEAN algorithm engine event flow와 thread 차이](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)

NautilusTrader의 backtest engine은 chronological event replay, simulated exchange와 performance
analysis를 제공한다. report surface는 order, fill, position, account state DataFrame을
backtest/live에서 같은 형식으로 제공한다. 공식 문서는 bar-only replay, order book type,
latency와 live 차이를 함께 경고한다.

- [NautilusTrader backtest engine](https://github.com/nautechsystems/nautilus_trader/blob/b087d8ac1a3b1598b392845a165992f644a1fbda/crates/backtest/src/engine.rs#L79-L119)
- [NautilusTrader backtesting event order와 현실성 한계](https://nautilustrader.io/docs/latest/concepts/backtesting/)
- [NautilusTrader structured reports](https://nautilustrader.io/docs/latest/concepts/reports/)

### 4.3 OpenBB와 ArcticDB: 결과 표면과 재현성

OpenBB의 `OBBject`는 실제로 `results`, `provider`, `warnings`, `chart`, `extra`를 보유하고
results를 DataFrame으로 변환한다. Workspace 문서는 agent의 text/table/chart를 widget으로
저장해 다음 세션에서도 쓰는 사용자 표면을 설명한다. 다만 open-source Platform repository는
provider/result primitive가 중심이며 hosted agent 제품 전체 구현으로 오해하면 안 된다.

- [OpenBB OBBject 결과 계약](https://github.com/OpenBB-finance/OpenBB/blob/3e071fcc2cd9f891cac6040ae60296dba76dab46/openbb_platform/core/openbb_core/app/model/obbject.py#L36-L169)
- [OpenBB provider standardization](https://docs.openbb.co/odp/python/developer/standardization)
- [OpenBB AI-generated text/table/chart widget](https://docs.openbb.co/workspace/analysts/widgets/ai-generated-widgets)
- [OpenBB open-source agent manifest integration 범위](https://github.com/OpenBB-finance/OpenBB/blob/3e071fcc2cd9f891cac6040ae60296dba76dab46/openbb_platform/extensions/platform_api/openbb_platform_api/utils/merge_agents.py#L1-L53)

ArcticDB의 `VersionedItem`은 data와 함께 resolved version, metadata, write timestamp를
반환하며 snapshot은 여러 source/derived symbol을 같은 이름으로 고정한다. 이 원칙은 필요하지만
ArcticDB 자체는 사용자-facing 연구 결과를 만들지 않는다.

- [ArcticDB time travel과 snapshot](https://github.com/man-group/ArcticDB/blob/3c3c6cb1a5290a83aeaab95c539a4ee1a1dbaefe/README.md#L29-L42)
- [ArcticDB named snapshot](https://github.com/man-group/ArcticDB/blob/3c3c6cb1a5290a83aeaab95c539a4ee1a1dbaefe/docs/mkdocs/docs/tutorials/snapshots.md#L1-L35)
- [ArcticDB VersionedItem](https://github.com/man-group/ArcticDB/blob/3c3c6cb1a5290a83aeaab95c539a4ee1a1dbaefe/python/arcticdb/version_store/_store.py#L204-L239)

### 4.4 FinNLP와 현재 AI4Finance 비교

FinNLP의 확인 가능한 산출물은 뉴스·공시 DataFrame이다. persistent actor가 가설을 만들고
실험하고 feedback으로 다음 행동을 정하는 구현 근거는 찾지 못했다. 따라서 최초 기획에 있던
FinNLP는 source coverage 참고로만 유지한다.

- [FinNLP 뉴스 DataFrame](https://github.com/AI4Finance-Foundation/FinNLP/blob/be4dfd5c2526e88bbb1307d444086c46a4b47e3a/README.md#L13-L42)
- [FinNLP SEC DataFrame](https://github.com/AI4Finance-Foundation/FinNLP/blob/be4dfd5c2526e88bbb1307d444086c46a4b47e3a/README.md#L233-L268)

확장 조사한 현재 AI4Finance 프로젝트 중 FinRobot은 evidence link, numeric provenance,
multi-agent synthesis와 HTML/PDF report를 전면에 둔다. 이는 결과물 형식의 참고다. FinRL과
FinRL-X는 CSV/model/PNG와 replay/Paper chart를 만들지만 전략 발견 actor는 아니다.

- [FinRobot research/synthesis/report pipeline](https://github.com/AI4Finance-Foundation/FinRobot/blob/01ed408326f1d4ec2460596dee10858faf0f69af/README.md#L67-L103)
- [FinRobot HTML/PDF와 charts](https://github.com/AI4Finance-Foundation/FinRobot/blob/01ed408326f1d4ec2460596dee10858faf0f69af/README.md#L173-L193)
- [FinRL CSV/model/backtest PNG](https://github.com/AI4Finance-Foundation/FinRL/blob/2334a5fe6d30629157f13c3b0319e1637e15e123/README.md#L140-L162)
- [FinRL-X backtest/Paper charts](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/e65d6f0483ead7d2ef4a5fc940cdf960392a25c1/README.md#L142-L177)

## 5. 여섯 agent가 매 cycle 남겨야 하는 실제 결과물

공통 result envelope는 OpenBB 방식의 사용자 결과와 RD-Agent/Qlib 방식의 연구 lineage를
결합한다.

| 필드 | 필수 내용 |
|---|---|
| identity | agent, cycle, timestamp, market/session |
| question | 이번 cycle이 답하려는 질문 또는 falsifiable hypothesis |
| evidence | provider/source, 관측시각, dataset snapshot/as-of/resolved version, immutable hash |
| action | 선택한 tool, 입력 config/code version, 실행 status, stdout/stderr/exception ref |
| results | 사용자에게 읽히는 narrative와 machine-readable rows |
| metrics | 사용한 경우 baseline, comparison, 비용 포함 metric과 limitations |
| chart | 결과 이해에 필요한 경우 chart spec 또는 artifact ref |
| feedback | accept/reject/inconclusive, 이유, 새 관찰과 반증 |
| recommendation | 있을 때만 timestamp, entry, stop, targets, rationale, outcome history ref |
| continuation | 다음 질문, 다음 wake 조건 또는 terminal reason |

`process alive`, `launcher exit 0`, `receipt emitted`, `schema valid`만으로는 이 결과물이 아니다.
`no_action`도 evidence, reason, 다음 확인 조건이 있어야 사용자 결과로 인정하며 research-active
cycle에는 합산하지 않는다.

## 6. Agent별 사용자-facing artifact

| Agent | 한 cycle의 최소 실제 결과물 |
|---|---|
| Opportunity Manager | 후보 table, 각 후보의 source/evidence, 조사 이유, 중복 판단, 다음 가설 |
| Market Context | regime narrative, breadth/volatility/liquidity table 또는 chart, 전략별 영향과 한계 |
| Day | setup/recommendation/no-action card, 최신 completed bar, entry/stop/targets, 실행·outcome 상태 |
| Swing | multi-session thesis, catalyst/evidence timeline, invalidation, open-state review |
| Systematic Quant | falsifiable hypothesis, baseline, experiment config, metric table/chart, reviewer feedback, next hypothesis |
| Derivatives Research | IV/skew/term/basis table 또는 chart, 데이터 entitlement, 해석·한계·후속 연구 |

## 7. 구현 우선순위에 미치는 영향

1. 공통 runtime이나 새 journal부터 만들지 않는다.
2. 기존 autonomous executor, experiment ledger, replay/tool과 Hermes를 사용해 Opportunity 한
   cycle이 실제 evidence에서 후보 table과 research card를 만들게 한다.
3. 바로 이어 Systematic 한 cycle이 source-backed hypothesis, bounded experiment, terminal
   result, Reviewer feedback과 next hypothesis를 만들게 한다.
4. 두 vertical에서 반복된 최소 state만 공통 persistent actor cycle로 추출한다.
5. Context, Day, Swing, Derivatives를 같은 result envelope에 연결한다.
6. 여섯 family 각각 실제 결과가 Hermes에서 조회되기 전에는 launcher/runtime completion을
   milestone으로 보고하지 않는다.

## 8. 통과 기준

첫 milestone은 다음 실제 artifact 묶음 두 개로 판정한다.

1. **Opportunity:** 실제 최신 source record → 후보 table → 근거 narrative → 후속 hypothesis 또는
   명시적 no-action → Hermes card → next wake.
2. **Systematic:** 실제 research source → falsifiable hypothesis → code/config hash → bounded run →
   terminal metrics/table/chart → Reviewer decision → next hypothesis → Hermes report.

두 결과 모두 provider/source, timestamp, dataset version/as-of, tool action, failure/limitations를
보여야 한다. fixture, QA schedule, synthetic payload, launcher health, start-only trial은 통과 증거가
아니다.
