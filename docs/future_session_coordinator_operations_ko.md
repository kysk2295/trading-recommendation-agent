# Future Session Coordinator 운영 절차

이 서비스는 US/KR 다음 거래일 계획을 정확한 `main` SHA에 고정해 준비하고 실행한다. 주문 변경 권한은 Alpaca Paper 전용 guard와 risk kernel을 통과한 경우에만 허용되며, KIS와 LS는 계속 조회 전용이다.

## 안전 경계

- bootstrap manifest, 생성된 config, US/KR template은 현재 사용자 소유의 일반 파일이며 mode `600`이어야 한다.
- coordinator config/report는 schema v3이며, config가 고정한 US/KR template SHA-256과 실제 canonical template 바이트가 다르면 계획·활성화 전에 차단된다.
- bundle, state root, LaunchAgents 디렉터리는 현재 사용자 소유의 실제 디렉터리이며 mode `700`이어야 한다. 심볼릭 링크와 공개 디렉터리는 거부된다.
- bundle, state root, LaunchAgents, authority repository는 서로 같거나 조상·자손 관계일 수 없다.
- manifest의 `scheduler_main_sha`는 clean local `main` 및 `origin/main`과 정확히 같아야 한다.
- poll interval은 1초 이상 3600초 이하만 허용한다.
- US template의 `paper_auto_arm_policy`는 수동 Paper pilot 승인 receipt가 검토되기 전까지 설정하지 않는다. champion이 없거나 세션 권한이 맞지 않으면 auto-arm은 계속 fail-closed 상태다.
- 어떤 단계에서도 Alpaca live URL, KIS/LS 주문·잔고·계좌 변경 API를 사용하지 않는다.

## 최초 설치

1. 검토된 schema-v1 bootstrap manifest를 mode `600`으로 준비한다. manifest는 서로 다른 US/KR `FutureSessionPlanRequest`, versioned `bundle_path`, private `state_root`, private `launch_agents_dir`, authority repository, 정확한 SHA, poll interval을 포함한다.
2. 아래 명령은 config와 두 template을 하나의 staging 디렉터리에 기록하고 fsync한 뒤 bundle 디렉터리를 원자적으로 공개한다. 같은 manifest 재실행은 바이트가 동일할 때만 성공한다.

   ```text
   uv run --offline python run_future_session_coordinator_service.py bootstrap --manifest /absolute/private/bootstrap.json
   ```

3. 반환된 config로 서비스를 활성화한다.

   ```text
   uv run --offline python run_future_session_coordinator_service.py activate --config /absolute/versioned/bundle/coordinator.json
   ```

4. `activate`는 plist 등록만으로 성공하지 않는다. 새 프로세스가 기록한 config digest, template digest, scheduler SHA, service start, 관찰 시각이 활성화 경계보다 새롭고 `ready`이며 US/KR 어느 시장도 `blocked`가 아닌 report가 확인돼야 성공한다.
5. `status`는 config/SHA가 다르거나, 미래 시각이거나, poll interval 두 배보다 오래된 report를 거부한다.

   ```text
   uv run --offline python run_future_session_coordinator_service.py status --config /absolute/versioned/bundle/coordinator.json
   ```

## SHA 업그레이드

1. 새 `main` SHA마다 기존 bundle/state/LaunchAgents 경로와 다른 candidate 경로를 사용해 새 manifest를 준비한다.
2. candidate에 `bootstrap`을 실행해 config/template/plist/frozen runtime을 먼저 검증한다. 이 단계는 launchd를 변경하지 않는다.
3. 현재 config와 candidate config를 지정해 교체한다.

   ```text
   uv run --offline python run_future_session_coordinator_service.py replace --current-config /absolute/current/coordinator.json --candidate-config /absolute/candidate/coordinator.json
   ```

4. replace는 현재 bundle의 설치 plist와 preparation manifest를 합쳐 소유 자식 label을 조사한다. 하나라도 launchd에 남아 있거나 inventory를 안전하게 읽을 수 없으면 현재 coordinator를 내리기 전에 실패한다.
5. 자식이 없을 때만 현재 job을 내리고 같은 inventory를 다시 확인한다. 중지 경계에서 자식 상태가 바뀌었으면 candidate를 시작하지 않고 이전 descriptor-pinned coordinator와 fresh health를 복구한다.
6. 두 확인이 모두 통과하면 freshness 경계를 기록하고 descriptor로 고정된 candidate plist를 bootstrap한다. candidate의 fresh matching health가 없으면 candidate를 내리고 이전 descriptor-pinned plist를 복구한 뒤 이전 config의 fresh health도 확인한다. candidate 또는 복구된 current의 중지를 확인할 수 없으면 `*_bootout_failed`를 별도로 기록하고 다음 서비스를 시작하지 않는다.
7. 성공 후에도 이전 bundle/state/plist는 다음 운영 검증이 끝날 때까지 보존한다.

schema v2 coordinator config는 template digest가 없으므로 schema v3 `replace` 입력으로 허용되지 않는다. 실제 배포된 v2 coordinator가 없는 현재 전환에서는 새 v3 bundle을 bootstrap해 최초 활성화한다. 향후 SHA 교체는 v3끼리만 수행한다.

## 재시작과 장애 확인

동일 SHA의 frozen runtime을 재시작할 때는 `restart`를 사용한다. mutable `main`이 이후 이동했더라도 compiler는 명시적인 `frozen_runtime` authority mode에서 clean exact-commit runtime을 다시 검증하며, 재시작 후 fresh health가 없으면 job을 다시 내린다.

```text
uv run --offline python run_future_session_coordinator_service.py restart --config /absolute/current/coordinator.json
```

US one-shot 또는 KR supervisor가 실행 시점 frozen-runtime authority 검증에 실패하면 role별 mode `600` execution-incident receipt를 원자적으로 최초 1회만 기록한다. 다음 coordinator tick은 manifest/request/plan/SHA를 다시 검증한 뒤 Hermes `INCIDENT`와 Dashboard `blocked` session terminal로 투영한다. 유효하지 않은 incident나 Hermes 저장 실패는 service를 fail-closed로 만든다.

운영 완료 증거에는 redacted 명령 결과, fresh status report, US/KR 각각의 계획·terminal receipt, Hermes/Dashboard reconciliation, 재시작, provider fault, 중복·누락 terminal 점검을 포함한다. Paper provider 확인은 GET-only reconciliation부터 수행하며, 수동 pilot 승인 전에는 주문을 만들지 않는다.
