# 자율 Python 전략 생성·컨테이너 실험 루프 설계

- 상태: 사용자 실행 방식 승인 완료, 설계 검토 대기
- 선택일: 2026-08-02
- 선택한 방식: LLM이 일반 Python 전략 코드를 생성하고 격리 컨테이너에서 자동 실험
- 거래 경계: 연구·shadow와 Alpaca Paper Trading 전용, 실거래 금지

## 1. 결정 요약

현재 Researcher는 가설과 Python 초안을 만들지만 초안을 실제 전략으로 등록하거나 실행하지 않는다. 기존 trial은 `StrategyMode`에 등록된 네 가지 고정 전략만 실행한다. 이번 변경은 생성된 Python 코드를 불변 artifact로 보존하고, 네트워크가 차단된 OCI 컨테이너에서 실행하여 기존 walk-forward, Reviewer, experiment ledger, 피드백 루프에 연결한다.

사용자가 선택한 "전부 허용"은 전략 표현력을 뜻한다. 생성 코드는 임의 함수, 클래스, 상태, 지표 계산과 Python 제어 흐름을 사용할 수 있다. AST allowlist나 전략 DSL로 계산 논리를 제한하지 않는다. 반면 생성 코드에 호스트 권한, 비밀키, 네트워크 또는 브로커 주문 권한을 주지는 않는다. 자유로운 전략 계산과 시스템 권한은 서로 다른 경계다.

핵심 결정은 다음과 같다.

1. 생성 코드는 호스트 Python 프로세스에서 `import`, `compile`, `eval` 또는 실행하지 않는다.
2. 한 전략 artifact는 원본 source, manifest, LLM receipt, hypothesis lineage와 SHA-256 identity로 구성한다.
3. 코드는 digest로 고정한 전용 research image 안에서 비루트 사용자로 실행한다.
4. 컨테이너는 네트워크, 비밀키, Docker socket, 호스트 repository와 브로커 모듈을 받지 않는다.
5. 컨테이너 출력은 주문이 아니라 bar별 `StrategySignal` 후보뿐이다.
6. 거래 결과, 같은 봉 충돌, 비용, bootstrap과 Reviewer 판정은 기존 호스트 평가기가 계산한다.
7. 생성 전략은 historical과 shadow lifecycle부터 시작하며 기존 승인·Risk Kernel을 우회해 Paper 주문으로 이동할 수 없다.
8. Researcher는 실패와 Reviewer 결과를 다음 bounded cycle의 입력으로 사용한다.
9. 컨테이너 런타임이 없거나 안전 옵션을 증명하지 못하면 실행을 fail closed 한다.

## 2. 현재 결손

현재 구현에는 다음 단절이 있다.

- `CandidateStrategyDraft.source_code`는 receipt에 남지만 hypothesis manifest와 queue의 실행 대상이 아니다.
- `IntradayHypothesisSelection.strategy`와 `IntradayWalkForwardRequest.strategy`는 닫힌 `StrategyMode` enum이다.
- `run_intraday_walk_forward`는 `build_strategy(StrategyMode)`만 호출한다.
- 실제 LLM이 만든 코드가 문법적으로 유효해도 fixed template로 다시 매핑되므로 새 전략을 시험하지 않는다.
- Reviewer 결과와 trial 실패는 다음 Researcher context에 들어가지만, 생성 artifact 단위의 실행 실패와 재현성 정보는 없다.

따라서 현재 시스템은 "LLM이 가설을 제안하고 기존 전략을 시험"하는 단계이며, 목표는 "LLM이 전략 구현을 만들고 그 구현 자체를 격리 실행·평가"하는 단계다.

## 3. 목표와 비목표

### 3.1 목표

- LLM이 작성한 일반 Python 전략을 자동으로 artifact화한다.
- 생성 코드를 호스트와 분리된 컨테이너에서 bounded historical trial로 실행한다.
- source, image, 입력 데이터, evaluator, 결과와 Reviewer 결정을 하나의 재현 가능한 계보로 연결한다.
- 컴파일 오류, 런타임 오류, timeout, OOM, 프로토콜 위반과 비결정성을 모두 실패 이력으로 보존한다.
- Reviewer 피드백을 다음 Researcher 생성 사이클에 공급한다.
- 기존 고정 전략 실행과 원장을 깨지 않고 generated 전략 선택을 추가한다.
- 향후 승인된 generated 전략이 기존 Paper lifecycle을 사용할 수 있게 하되 생성 코드 자체에는 주문 권한을 주지 않는다.

### 3.2 비목표

- 생성 코드에 호스트 filesystem, 사용자 홈, credentials 또는 외부 인터넷 제공
- 생성 코드가 Alpaca, KIS, LS 또는 다른 provider를 직접 호출
- LLM이 evaluator, Risk Kernel, lifecycle gate 또는 audit ledger를 수정
- historical/backtest 성과를 수익성 보장으로 표현
- 자동 package 다운로드 또는 trial 중 이미지 빌드
- 컨테이너 결과만으로 즉시 Paper Champion 승격
- Alpaca live endpoint나 실자금 주문 지원

## 4. 검토한 접근법

### 4.1 Typed Strategy DSL

가장 안전하고 결정론적이지만 표현력이 사전 정의된 지표와 연산자로 제한된다. 사용자가 제한형을 원하지 않아 기각한다.

### 4.2 호스트의 제한된 Python subprocess

구현은 작지만 Python 수준 제한은 보안 경계가 아니다. 생성 코드가 호스트 파일, 네트워크와 credentials를 탐색할 위험이 있어 기각한다.

### 4.3 OCI 컨테이너의 일반 Python 코드

전략 논리는 일반 Python으로 유지하면서 OS 자원과 권한을 외부에서 제한할 수 있다. 컨테이너 탈출 위험은 완전히 0이 아니지만, digest-pinned image, 비루트 실행, capability 제거, 네트워크 차단과 read-only mount를 결합하면 현재 요구에 가장 적합하다. 이 방식을 채택한다.

## 5. 신뢰 경계

시스템을 세 영역으로 분리한다.

### 5.1 신뢰하는 호스트 영역

- Researcher call receipt와 immutable artifact store
- point-in-time bar selection과 completed-bar 검증
- experiment ledger와 lifecycle controller
- 비용·체결·stop/target 충돌·bootstrap 계산
- Reviewer와 Paper Risk Kernel
- 컨테이너 command builder와 결과 schema validator

### 5.2 신뢰하지 않는 generated-code 영역

- LLM이 만든 모든 Python source
- source가 import하는 research image 내부 package
- source의 stdout, stderr와 결과 JSON
- source가 계산한 rationale, signal과 내부 상태

생성 코드의 결과는 구조 검증과 시장 시점 검증을 통과하기 전까지 데이터로만 취급한다.

### 5.3 외부 변경 가능 영역

- Alpaca Paper API만 기존 broker boundary를 통해 변경 가능하다.
- 생성 컨테이너는 provider 및 broker boundary에 연결되지 않는다.
- KIS, OpenDART, LS와 모든 비-Alpaca 공급자는 계속 read-only다.

## 6. 생성 전략 artifact 계약

새 `GeneratedStrategyArtifact`는 최소한 다음 필드를 가진 frozen Pydantic 모델이다.

- `schema_version`
- `artifact_id`: canonical manifest와 source로 계산한 SHA-256
- `source_sha256`
- `source_filename`
- `entrypoint`: 고정값 `strategy:create_strategy`
- `hypothesis_id`
- `queue_card_key`
- `research_source_keys`
- `prompt_sha256`, `response_sha256`, `model_id`
- `python_version`
- `runner_protocol_version`
- `research_image_digest`
- `created_at`

artifact directory에는 `strategy.py`, canonical `manifest.json`과 receipt reference만 저장한다. 파일은 private immutable publication helper로 한 번만 만들며 동일 identity에 다른 bytes를 쓸 수 없다. 전략 version은 사람이 지은 이름이 아니라 `generated-python:<artifact_id>`로 계산한다.

런타임 dependency는 trial 도중 설치하지 않는다. 허용 package 집합은 digest로 고정된 research image가 제공한다. package 추가는 새 image digest와 새 전략 version을 만드는 별도 host-side 변경이다. 이는 전략 로직을 DSL로 제한하는 것이 아니라 공급망과 재현성을 고정하는 조치다.

## 7. 생성 코드 인터페이스

`strategy.py`는 다음 entrypoint를 제공한다.

```python
def create_strategy(context: dict[str, object]) -> object:
    ...
```

반환 객체는 다음 메서드를 제공한다.

```python
def observe(bar: dict[str, object], candidate: dict[str, object] | None) -> dict[str, object] | None:
    ...
```

객체 내부 구현은 자유롭다. 사용자 정의 함수, 클래스, 상태와 image에 포함된 라이브러리를 사용할 수 있다. runner는 completed bar를 시간 순서대로 전달한다. 반환값은 host의 `StrategySignal` schema와 정확히 일치해야 하며 symbol, timestamp, strategy, entry, stop과 rationale를 포함한다. 1R/2R target은 이 신호를 받은 host Risk 계산이 기존 규칙으로 파생한다.

컨테이너에는 전체 미래 데이터나 다음 봉을 한꺼번에 주지 않는다. 호스트는 stdin framed protocol로 현재 완료된 단일 bar와 point-in-time candidate를 보내고 해당 bar의 응답을 받은 다음에만 다음 bar를 보낸다. generated code가 stdin을 직접 읽으려 해도 호스트가 미래 frame을 아직 보내지 않았으므로 미래를 얻을 수 없고, 응답하지 않으면 timeout으로 실패한다. 반환 timestamp가 입력 bar timestamp와 다르거나 다른 symbol을 참조하면 protocol failure다.

## 8. 컨테이너 실행 계약

### 8.1 런타임 preflight

호스트는 실행 전에 다음을 모두 확인한다.

- 설정된 OCI CLI가 승인된 executable path와 일치한다.
- runtime daemon이 응답한다.
- image reference가 tag가 아닌 승인된 `sha256:` digest다.
- container command에 필수 isolation option이 모두 포함된다.
- source mount가 resolve된 bounded artifact directory 아래에 있다.
- source mount 대상에 symlink, credentials 또는 repository root가 없다.

하나라도 증명하지 못하면 container를 시작하지 않고 `container_preflight_failed`로 검열한다.

### 8.2 필수 isolation

Docker 호환 CLI의 의미상 command는 다음 제한을 모두 포함한다.

- `--rm`
- `--network none`
- `--read-only`
- 비루트 numeric `--user`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- bounded `--memory`, `--cpus`, `--pids-limit`
- 크기가 제한된 `/tmp` tmpfs
- source directory 하나의 read-only bind mount
- allowlist 기반의 최소 환경변수
- stdout, stderr와 결과 파일 크기 제한
- wall-clock timeout과 container ID 기반 강제 종료·정리

Docker socket, 사용자 홈, repository, SSH agent, cloud metadata, provider credential file과 host `/tmp`는 mount하지 않는다. `--privileged`, host network, host PID/IPC namespace, device mount와 추가 capability는 항상 거부한다.

### 8.3 입력과 출력

호스트와 runner는 stdin/stdout의 길이 제한 canonical JSON frame으로 통신한다. 호스트는 handshake가 끝난 뒤 bar 하나를 보내고 정확히 하나의 `signal` 또는 `no_signal` 응답을 받은 다음에만 다음 bar를 보낸다. runner는 generated module을 container 내부에서만 import하며 전략 객체는 한 fold 동안 메모리 상태를 유지한다. 전체 input file이나 미래 bar는 container filesystem에 존재하지 않는다.

호스트는 각 frame의 schema, 크기, 순서, symbol, timestamp, 유한 숫자, entry/stop 관계와 input lineage를 검증한다. 생성 코드가 protocol stdout을 임의로 오염시키거나 frame을 추가하면 즉시 실패한다. stderr는 별도로 크기를 제한해 receipt에 저장한다. 컨테이너가 계산한 PnL, 승률, target, 승격 판정 또는 주문 요청은 무시한다.

### 8.4 재현성 검사

동일 artifact, image digest, input digest와 seed로 최소 두 번 실행해 canonical signal stream SHA-256이 동일해야 한다. Python hash seed와 runner seed는 고정한다. 시간이나 난수를 사용하는 전략도 고정 입력에서 같은 결과를 내도록 작성할 수 있지만, 재실행 결과가 다르면 `non_deterministic_strategy`로 실패한다.

## 9. Walk-forward 통합

기존 `StrategyMode`를 제거하지 않고 실행 선택을 discriminated union으로 확장한다.

- `BuiltinStrategySelection`: 기존 `StrategyMode`와 parameter variant
- `GeneratedStrategySelection`: artifact ID, source hash, image digest와 runner protocol

research manifest는 새 schema version에서 두 선택을 모두 받을 수 있다. 기존 schema와 실행 경로는 그대로 유지한다.

generated selection은 fold마다 컨테이너 runner를 실행해 signal stream을 만든다. signal 이후의 recommendation 상태, stop/target 판정, same-bar collision, time exit, 거래 비용과 metrics는 호스트 evaluator가 처리한다. 따라서 generated code가 유리한 체결 순서를 주장하거나 비용을 생략할 수 없다.

trial ID는 strategy version, data version, manifest hash, image digest와 evaluator version을 포함해 계산한다. 재실행 시 동일 terminal event가 있으면 기존 artifact를 반환하고 코드를 다시 실행하지 않는다.

## 10. 원장과 lifecycle

Critic 승인 뒤 다음 순서로 기록한다.

1. LLM call receipt와 원문 response
2. hypothesis card와 cited source lineage
3. generated source artifact
4. strategy version registration
5. historical trial registration과 STARTED event
6. COMPLETED, FAILED 또는 CENSORED terminal event
7. Reviewer decision과 lifecycle event
8. 다음 cycle용 failure/review digest

artifact publication이나 ledger append가 실패하면 다음 단계로 이동하지 않는다. 실패한 generated strategy도 삭제하지 않으며 source hash와 reason code를 보존한다.

새 generated version의 초기 상태는 research-only다. historical 결과만으로 Paper 승격하지 않는다. 기존 shadow evidence budget, reviewer minimum session/trade 기준, freshness, feed entitlement와 lifecycle transition을 모두 통과해야 한다.

## 11. 자율 Researcher 루프

한 cycle은 다음 bounded state machine이다.

```text
context -> propose -> critique -> artifactize -> preflight -> trial
        -> deterministic replay check -> review -> feedback -> stop
```

- cycle당 proposal은 최대 3회다.
- 승인된 artifact는 cycle당 최대 1개만 heavy trial에 진입한다.
- heavy empirical lease는 기존 규칙대로 하나만 획득한다.
- timeout/OOM/runtime unavailable은 무한 재시도하지 않고 terminal reason으로 기록한다.
- 다음 cycle은 rejected hypothesis, failed falsification, runtime failure, Reviewer 근거와 기존 strategy hashes를 prompt에 포함한다.
- 동일 source hash나 의미상 동일 hypothesis는 Critic이 중복으로 거부한다.
- scheduler는 bounded one-shot CLI를 호출하며 장시간 resident LLM process를 두지 않는다.

## 12. Paper Trading 연결 경계

generated code는 Paper 단계에서도 직접 실행 권한이나 HTTP client를 받지 않는다. lifecycle이 승인한 artifact를 동일 pinned image와 runner protocol로 shadow/recommendation 계산에 사용하고, host가 검증한 `StrategySignal`만 기존 Risk Kernel에 넘긴다.

Risk Kernel이 만든 order intent만 기존 Alpaca Paper client에 도달할 수 있다. client는 네트워크 요청 전 trading base URL이 정확히 `https://paper-api.alpaca.markets`인지 검사한다. 다른 URL, live credential, closed session, stale feed, missing spread와 incomplete bar는 기존 규칙대로 차단한다.

초기 구현 완료 기준은 historical generated-code trial과 Reviewer feedback loop까지다. Paper bridge는 기존 lifecycle evidence가 실제로 충족된 전략이 생긴 뒤 별도 활성화하며, bridge를 미리 자동 활성화하지 않는다.

## 13. 실패 의미론

다음 reason code를 구분한다.

- `generated_artifact_invalid`: manifest, lineage 또는 hash 불일치
- `container_runtime_unavailable`: 승인 runtime/daemon 없음
- `container_preflight_failed`: image 또는 isolation 증명 실패
- `generated_strategy_import_failed`: container 내부 import/entrypoint 실패
- `generated_strategy_protocol_failed`: 입력·출력 계약 위반
- `generated_strategy_timeout`: wall-clock 초과
- `generated_strategy_oom`: memory limit 종료
- `generated_strategy_output_exceeded`: stdout/stderr/frame cap 초과
- `non_deterministic_strategy`: 동일 입력 재실행 hash 불일치
- `bounded_historical_experiment_failed`: 호스트 evaluator 실패

인프라 미준비와 데이터 부족은 `CENSORED`, generated code나 evaluator 실행 실패는 `FAILED`로 기록한다. terminal event를 남기지 못한 trial은 성공으로 간주하지 않는다.

## 14. 검증 전략

### 14.1 단위 테스트

- artifact canonicalization과 tamper rejection
- generated/builtin selection parsing과 identity
- OCI command의 필수 isolation option 및 금지 option
- symlink, broad mount, mutable tag, 잘못된 runtime path 거부
- input/output protocol과 size/time/resource limit 해석
- failure-to-ledger reason mapping
- 동일 입력 signal hash 재현성 검사

### 14.2 통합 테스트

- fake OCI runtime으로 propose -> artifact -> trial -> Reviewer -> feedback 전체 연결
- 기존 builtin strategy trial의 회귀 없음
- generated trial의 STARTED/COMPLETED/FAILED/CENSORED append-only chain
- lookahead timestamp, NaN, 다른 symbol과 fabricated PnL 거부
- 같은 봉 stop/target collision이 host에서 stop으로 확정됨
- generated code가 broker mutation 경로에 직접 도달할 수 없음

### 14.3 실제 컨테이너 수동 QA

- CLI help
- 존재하지 않는 runtime 또는 mutable image tag의 bad input
- 네트워크 접근과 host file 읽기를 시도하는 adversarial strategy가 실패함
- 정상 stateful Python strategy가 실제 pinned container에서 signal을 만들고 bounded trial과 Reviewer까지 완료됨
- timeout과 memory bomb가 제한 안에서 종료되고 terminal ledger event가 남음

현재 개발 호스트에서는 `docker`, `podman`, `container`, `nerdctl`, `colima`, `lima` executable을 찾지 못했다. 구현은 runtime 부재 시 fail closed 해야 하며, 실제 컨테이너 happy-path QA를 완료하려면 사용자가 선택한 OCI runtime을 별도로 설치·시작해야 한다. fake runtime 테스트만으로 실제 격리 검증을 통과했다고 주장하지 않는다.

## 15. 수용 기준

다음 조건을 모두 만족해야 기능 완료로 본다.

1. 실제 LLM response의 `strategy_source`가 immutable generated artifact가 된다.
2. artifact hash가 strategy version, trial, result와 Reviewer decision까지 이어진다.
3. generated source는 호스트에서 실행되지 않는다.
4. 실제 OCI container가 필수 isolation option으로 정상 전략을 실행한다.
5. adversarial network/file/process/resource 시도가 경계 밖으로 나가지 못한다.
6. generated signal을 기존 host evaluator가 보수적으로 평가한다.
7. 실패와 Reviewer 결과가 다음 Researcher context에 나타난다.
8. 기존 builtin trial과 experiment ledger 호환성이 유지된다.
9. Ruff, basedpyright, targeted/full pytest와 CLI help/bad/happy path가 통과한다.
10. Paper endpoint guard와 Risk Kernel을 우회하는 새 경로가 존재하지 않는다.

## 16. 잔여 위험

- 컨테이너는 VM과 동일한 보안 경계가 아니며 runtime/kernel 취약점에 의한 탈출 위험이 남는다.
- image에 포함된 package의 공급망 위험은 image digest 고정만으로 제거되지 않는다.
- 재현 가능한 전략도 데이터 스누핑이나 과최적화를 할 수 있으므로 Reviewer와 다중검정 통제가 계속 필요하다.
- 자유로운 Python은 DSL보다 실행 비용과 failure mode가 많다.
- 실제 OCI runtime이 현재 호스트에 없으므로 설치 전에는 기능 구현을 완료해도 실제 격리 happy path를 증명할 수 없다.

이 위험은 source를 호스트에서 실행하지 않고, 최소 권한 컨테이너, immutable lineage, 기존 Reviewer/lifecycle/Risk Kernel을 유지하는 방식으로 완화한다.
