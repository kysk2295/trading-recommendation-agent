# 6-family Persistent Research Runtime 체크포인트

## 구현 범위

- 단일 상시 Runtime과 6개 독립 research actor의 wake, cursor, open-work, memory 정책
- evidence 중복 방지, interrupted-cycle 복구, 가족별 실패 격리와 고정 backoff
- Hermes 의사결정의 family isolation, 단일 행동 권한, 명시적 provider/model binding
- Systematic Quant의 LLM 전략 생성, generated-Python sandbox 실행, 실험 원장, Reviewer feedback 연결
- Hermes delivery/dashboard 투영과 agent-family 분리
- private immutable config 및 단일 macOS LaunchAgent provision/verify/activate 경계
- 모든 runtime/action 결과의 order/lifecycle/allocation authority false 및 broker mutation 0

## schema v2 운영 입력 결속

서비스 config는 `schema_version=2`만 허용하며 v1을 자동 변환하지 않는다. Systematic 입력은
`--systematic-input-activation`의 mode-600 activation 하나로만 결속한다. `ready`는 activation
pointer 계약과 그 참조 artifact digest가 유효하다는 상태이며, 상태 조회 자체가 전체 evidence graph를
재심사했다는 뜻은 아니다. ready 상태는 결속된 CSV와 data-foundation digest를 노출하고, `blocked`는 digest를
`null`로 보고한다. 상태 JSON에는 로컬 경로가 포함되지 않는다. activation이 `blocked`여도 다른
다섯 research family는 계속 armed 상태로 동작하고, Systematic heavy action만
`production_input_unavailable`로 실패한다. 이 상태는 실험 성과나 수익성을 뜻하지 않는다.

```bash
uv run python run_research_agent_runtime.py provision \
  --project-root /absolute/trading-recommendation-agent \
  --uv-path /absolute/bin/uv \
  --hermes-executable /absolute/bin/hermes \
  --python-executable /absolute/bin/python \
  --model-id MODEL_ID --provider-id PROVIDER_ID \
  --cycle-database /private/state/research-cycles.sqlite3 \
  --output-root /private/state/reports \
  --hermes-database /private/state/hermes.sqlite3 \
  --config /private/config/research-runtime-v2.json \
  --plist /private/config/research-runtime-v2.plist \
  --source-outputs-root /absolute/outputs \
  --source-market-context-root /absolute/outputs/market-context \
  --source-day-session-root /absolute/outputs/live-sessions \
  --source-swing-shadow-database /absolute/outputs/swing/shadow.sqlite3 \
  --source-swing-review-database /absolute/outputs/swing/review.sqlite3 \
  --source-experiment-ledger /absolute/outputs/experiments/ledger.sqlite3 \
  --source-lane-review-database /absolute/outputs/reviews/lane.sqlite3 \
  --systematic-context /private/systematic/context.json \
  --systematic-experiment-ledger /private/systematic/experiment.sqlite3 \
  --systematic-receipt-root /private/systematic/receipts \
  --systematic-strategy-root /private/systematic/strategies \
  --systematic-manifest-root /private/systematic/manifests \
  --systematic-queue-root /private/systematic/queue \
  --systematic-input-activation /private/systematic/input-activation.json \
  --systematic-artifact-root /private/systematic/artifacts \
  --systematic-review-root /private/systematic/reviews \
  --systematic-runs-root /private/systematic/runs

uv run python run_research_agent_runtime.py verify \
  --config /private/config/research-runtime-v2.json \
  --plist /private/config/research-runtime-v2.plist

uv run python run_research_agent_runtime.py status \
  --config /private/config/research-runtime-v2.json \
  --plist /private/config/research-runtime-v2.plist
```

## 실제 QA

QA root: `/private/tmp/persistent-research-runtime-qa.P3kNQO`

- 실제 Hermes binding: `provider=openai-codex`, `model=gpt-5.5`
- 운영 `outputs/`는 source evidence로만 읽고, cycle/Hermes/Systematic 쓰기는 QA root에 격리했다.
- 당시 불변 config와 plist는 모두 mode `600`, verify 통과. 현재 schema v2 cutover에서는 별도
  mode-600 activation 검증도 통과해야 한다.
- 실제 actor 실행 순서:
  1. `day_trading` — no_action, model call 1, broker mutation 0
  2. `systematic_quant` — no_action, model call 1, broker mutation 0
  3. `opportunity_manager` — no_action, model call 1, broker mutation 0
  4. `market_context` — no_action, model call 1, broker mutation 0
  5. `swing_trading` — no_action, model call 1, broker mutation 0
  6. `derivatives_research` — no_action, model call 1, broker mutation 0
- 다음 tick — `idle`, model calls 0, projected results 0, broker mutation 0.
- 별도 실제 Systematic cycle `systematic-manual-2`:
  - LLM이 Python 전략 artifact를 생성했다.
  - sandbox에서 전략을 실행하고 가설 1건, trial 1건, trial event 2건을 기록했다.
  - experiment artifact와 review artifact를 발행했다.
  - Reviewer decision `hold`, next feedback `hold`, trading mutation 0.

## 실제 QA 중 수정한 결함

1. 사용자 설정을 차단한 Hermes 호출에 provider binding이 없어 기본 provider/model 선택이 실패했다.
   - 서비스 config에 `provider_id`를 추가하고 decision과 proposal 명령 모두 `--provider`를 명시했다.
2. JSON Schema에 나타나지 않는 decision 교차 필드 규칙 때문에 정상 JSON이 fail-closed 됐다.
   - `no_action`과 `scheduled`의 필수 조합을 prompt contract에 명시했다.
3. `strategy_source` 실행 프로토콜이 모호해 모델이 Python 대신 자연어 설계를 생성했다.
   - 완전한 Python source, 입력 키, exact signal 반환 키와 가격 제약을 prompt contract에 명시했다.

## 검증

- 기능 집중 테스트: `79 passed`
- Ruff: clean
- basedpyright: 0 errors, 0 warnings, 0 notes
- 전체 테스트 at code SHA `6daaf6816652db74be81ed10513d31f2d2efeeda`:
  - `4208 passed, 5 failed in 321.78s`
  - 실패 5건은 작업 전 기준선과 동일한 `tests/test_dashboard_publisher_system_authority.py` 항목이며 이번 변경 범위와 무관하다.
- Alpaca Paper mutation 관련 집중 테스트: `31 passed`
- 실제 broker/trading mutation: 0

## 구현 커밋

- `6199043` cycle/evidence/result 모델
- `60928d2` append-only store, cursor, recovery
- `3fb2274` family source adapters
- `672cdf0` independent wake policy
- `cca3a63` Hermes decision boundary
- `f4866fb` family action 및 Systematic 연결
- `451706a` persistent runtime
- `7daca01` Hermes/dashboard family isolation
- `5bbf587` private LaunchAgent service
- `cd91c50` explicit Hermes provider binding
- `53082c5` decision cross-field contract
- `6daaf68` executable strategy-source protocol
