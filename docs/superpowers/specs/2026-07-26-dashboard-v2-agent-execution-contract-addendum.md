# Dashboard v2 Agent Execution Contract Addendum

- 상태: 기존 Dashboard v2의 dashboard-wide `explicit-submit-only` 회귀를 폐기하고 최상위
  Quant Research OS 계약에 맞게 교정
- 권위: agent identity, interactive/autonomous execution, control-plane receipt,
  비용·안전 계약
- 상위 계약:
  [기관형 다중 시장 Quant Research OS 설계](2026-07-17-institutional-multi-market-quant-research-os-design.md)

## 1. 교정 결정과 정확한 agent identity

Dashboard v2의 LLM-backed product research family는 정확히 다음 여섯 개다.

```text
opportunity_manager
day_trading
swing_trading
systematic_quant
derivatives_research
market_context
```

이 목록은 market domain, strategy lane, process group과 독립적인 제품 identity다.
`allocation_manager`는 두 개 이상의 독립 champion이 persist된 뒤에만 조건부로 활성화되는
하류 위험예산 역할이며 여섯 primary family에 포함하지 않는다. `Independent Reviewer`,
`Lifecycle Controller`, `Execution Engine`, `Loop Engineer`는 각각 심사, 상태 전이,
Paper 집행, 연구 workflow를 담당하는 control-plane role이지 product agent family가
아니다. `delivery`는 agent가 아니라 redacted projection/event transport다. In exact
identity terms, **delivery is not an agent**.

현재 launchd의 KR theme, US intraday, US systematic, US swing, research, delivery process
group을 여섯 identity에 일대일 또는 다대일로 매핑하는 것은 명시적으로 금지한다.
launchd group은 scheduler/runtime observability 대상일 뿐이며 agent identity authority가
아니다. family identity는 전용 registry와 typed receipt의 `agent_family_id`로만 정한다.

## 2. 모든 family가 반드시 제공하는 세 capability

여섯 family 각각은 예외 없이 다음 세 capability를 모두 제공해야 한다.

1. **지속 대화:** family별 로컬 장기 memory와 persistent interactive session을 갖고
   `hermes --resume <session_id>`로 이어진다. 명시적 사용자 메시지 한 건은 정확히 한
   interaction claim과 최대 한 Hermes process를 만든다. crash 또는 결과 불확실성에도
   자동 유료 재시도하지 않는다.
2. **사용자 지시 tool execution:** 사용자는 연구, 분석, 가설 등록, 실험, 허용된 code
   work를 지시할 수 있다. 실행은 generic one-shot text 응답이 아니라 typed directed-job
   claim, allowlisted tool step, streaming progress, append-only evidence, terminal result로
   나타난다. provider/Paper mutation은 기존 gate 밖에서 열리지 않는다.
3. **자율 LLM 연구 loop:** 사용자 submit 없이도 typed `new_data`, `market_event`,
   `experiment_result`, `reviewer_feedback`, `approved_schedule` trigger가 도착하면 연구를
   수행한다. interactive identity와 장기 memory를 공유하지만 매 trigger는 별도
   autonomous task session, claim, tool environment와 receipt chain을 사용한다.

## 3. 두 execution channel

### 3.1 Interactive Hermes channel

- 입력 권위는 paired operator의 명시적 사용자 메시지다.
- 한 `interaction_id`는 `accepted -> claimed -> process_started ->
  completed | failed | uncertain`으로만 전이한다.
- 동일 family의 persistent conversation과 memory를 `hermes --resume`으로 잇되,
  interaction별 process claim은 분리한다.
- command가 tool work를 요구하면 같은 interaction 아래 directed job과 step receipt를
  만들고 progress/evidence/result event를 stream한다.
- crash 전 launch 여부가 증명되지 않거나 process exit 뒤 terminal delivery가
  불확실하면 `uncertain`으로 닫는다. automatic paid retry는 없으며 사용자의 새 명시적
  메시지만 새 claim을 만들 수 있다.

### 3.2 Autonomous Research channel

- 입력 권위는 strict `AutonomousTriggerV1`뿐이다. 필수 필드는 `trigger_id`,
  `trigger_type`, `agent_family_id`, `source_receipt_ids`, `observed_at`,
  `authorized_at`, `policy_version`, `dedupe_key`, `budget_envelope`,
  `environment_spec`, `payload_sha256`이다.
- 허용 trigger type은 위 다섯 개뿐이다. 자유문, dashboard render, WebSocket heartbeat,
  filesystem noise, launchd group 이름은 trigger가 아니다.
- durable claim key는 `(agent_family_id, policy_version, dedupe_key)`이며 원자적
  compare-and-set으로 한 번만 claim한다. duplicate/replay/reconnect는 기존 receipt를
  반환하고 새 model process를 만들지 않는다.
- claim 전 family/day/token·cost budget, trigger cooldown, global/family concurrency,
  rolling failure budget을 확인한다. 어느 한 gate라도 실패하면 model 호출 없이 typed
  `blocked` receipt로 닫는다.
- 각 task는 clean pinned code SHA의 격리된 git worktree와 격리 experiment environment,
  allowlisted read/write roots, allowlisted tools, bounded runtime/network를 사용한다.
  integration worktree와 다른 task worktree를 수정하지 않는다.
- tool execution은 append-only input, step, stdout/stderr summary, artifact hash, result,
  cleanup receipt를 남긴다. raw secret, credential, account identity, local path, session ID,
  raw provider payload는 outbound 전에 redaction한다.
- provider mutation은 기존 Paper gate가 명시적으로 허용한 경로 외에는 금지한다.
  KIS/LS 및 다른 provider는 read-only이고 live money는 영구 금지다. Alpaca는 exact
  Paper endpoint와 기존 operator/arm/risk/reconcile/OCO/cutoff/EOD-flat gate를 모두
  통과한 별도 승인 없이는 mutation하지 않는다.
- autonomous 결과는 candidate evidence일 뿐이다. Independent Reviewer 판정과
  Lifecycle Controller gate 없이는 strategy version 승격, champion, allocation 또는
  실행권한을 바꿀 수 없다. code work는 테스트된 isolated patch/PR candidate와 immutable
  version evidence까지만 만들며 자동 merge/deploy하지 않는다.
- crash 시 launch가 증명된 task를 재호출하지 않는다. `failed` 또는 `uncertain` terminal
  receipt를 남기며 동일 trigger의 automatic paid retry는 금지한다. 새 source receipt나
  명시적으로 승인된 새 schedule occurrence만 새 dedupe key가 될 수 있다.

## 4. identity, memory, session 분리

family별 long-term memory namespace는 두 channel이 공유하되 redacted, versioned,
append-only memory event로만 갱신한다. Interactive channel의 persistent Hermes session
ID와 Autonomous channel의 task session ID는 서로 다르며 Railway로 보내지 않는다.
autonomous task는 interactive `--resume` process를 재사용하거나 대화 순서를 점유하지
않는다. memory write는 source receipt와 channel/task ID를 갖고 충돌 시 fail closed한다.
서로 다른 family는 memory/session/binding을 공유하지 않는다.

## 5. control-plane state와 receipt

| 객체 | 상태 |
| --- | --- |
| `InteractiveInteraction` | `accepted -> claimed -> process_started -> completed \| failed \| uncertain` |
| `DirectedJob` | `accepted -> claimed -> running -> completed \| failed \| uncertain \| blocked` |
| `AutonomousTrigger` | `observed -> authorized \| rejected -> claimed -> running -> completed \| failed \| uncertain \| blocked` |
| `ResearchCandidate` | `registered -> reviewer_pending -> accepted \| rejected \| needs_evidence` |
| `LifecycleDecision` | 기존 append-only lifecycle authority만 사용 |

각 receipt에는 public opaque ID, family ID, channel, causation/correlation ID, policy/code
version, timestamps, input/result/evidence hashes, budget consumed, redaction result와 terminal
reason이 들어간다. 상태는 compare-and-set으로 전진만 하고 terminal은 교체하지 않는다.
process와 tool-step progress는 append-only다. receipt가 없으면 UI는 실행을 추론하지
않으며 PID, launchd label, prose log, socket activity를 running/success로 승격하지 않는다.

## 6. Railway projection과 Command Center

Mac mini는 local session/binding/worktree/path/secret을 제거한 redacted projection과
streaming event만 Railway로 보낸다. Railway는 model을 실행하거나 autonomous trigger를
만들지 않으며 worker/poller를 추가하지 않는다. 허용 event kind는 interaction state,
directed-job progress/evidence/result, autonomous trigger/task progress/evidence/result,
Reviewer/lifecycle terminal과 projection snapshot이다.

Command Center는 동일 family 아래에서도 다음을 시각적으로 구별한다.

- **Conversation:** persistent interactive history와 현재 message claim.
- **Directed job:** 사용자 메시지에서 파생된 tool plan, step progress, evidence, result.
- **Autonomous job:** trigger type/source/schedule, budget gate, isolated environment, task
  progress, Reviewer/lifecycle 상태. 사용자 submit으로 위장하지 않는다.

System은 scheduler/trigger/claim/cooldown/budget/concurrency/failure/worktree/cleanup receipt를,
Research는 autonomous hypothesis/experiment/evidence와 Reviewer feedback을, Strategies는
family/lane/version/trial/Reviewer/lifecycle/champion 및 조건부 Allocation Manager gate를
관찰한다.

## 7. idle, 비용과 안전의 정확한 뜻

`idle zero model calls`는 **사용자 입력도 없고 authorized autonomous trigger도 없는 true
idle**에서 model call이 0이라는 뜻이다. dashboard의 주기적 HTTP/DB/model polling은
항상 0이다. authorized autonomous trigger는 budget/cooldown/concurrency/failure gate를
통과한 뒤 model을 호출할 수 있다. 따라서 “explicit submit 없이는 model call 금지”,
“automatic model execution 금지” 같은 dashboard-wide 문구는 폐기한다.

필수 cost/safety QA의 binary observable은 다음과 같다.

1. 5분 true-idle: periodic HTTP/DB requests `0`, interactive processes `0`, autonomous
   processes `0`.
2. explicit message 1건: interaction claims `1`, Hermes processes `1` 이하, paid retry `0`.
3. directed tool job: allowlisted step receipt와 streamed progress/evidence/result가 모두
   존재하고 generic text-only completion은 거부된다.
4. authorized trigger 1건과 duplicate 2건: autonomous claims `1`, model processes `1`
   이하, duplicate launch `0`.
5. unauthorized/invalid/budget-exhausted/cooldown/concurrency/failure-budget trigger: model
   processes `0`, typed blocker receipt `1`.
6. crash seam 전체: claim당 process `1` 이하, automatic paid retry `0`, terminal
   `failed | uncertain`.
7. autonomous code work: integration worktree diff `0`, isolated worktree receipt와 cleanup
   receipt 존재, append-only artifact hash 검증.
8. promotion attempt: Independent Reviewer와 Lifecycle decision이 없으면 authority change
   `0`; live-money/provider-forbidden mutation `0`; Paper mutation은 기존 gate 없으면 `0`.
9. outbound scan: credential/account/session/path/raw-payload leaks `0`.

## 8. 이전 승인 상태

2026-07-26 interactive observatory에서 승인된 pairing, private operator cookie, persistent
Hermes conversation, one-message/one-claim, no paid retry, redaction과 event-driven viewer
delivery는 Interactive Hermes channel에 한해 유지한다. 그 문서의 dashboard-wide
“There are no automatic model calls”와 “No model call without an explicit command submission”은
본 addendum로 폐기된다. Dashboard v2 master와 vertical plan은 이 교정 없이는 승인된
agent execution 설계로 간주하지 않는다. 기존 schema/showcase/visual acceptance는
영향받지 않는다.
