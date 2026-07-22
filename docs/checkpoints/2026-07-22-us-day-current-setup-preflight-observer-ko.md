# US Day current-setup preflight observer 체크포인트

기준 시각: 2026-07-22 07:44 EDT
코드 기준: `d0f03cfcab89963f10f245496ef26eb500a6017b`

## 배치 목적

`load_current_orb_paper_entry`는 현재 세션의 최신 완료 1분봉과 30초 이내 관측값만
허용한다. unattended 정규장에서 수동 preflight만 사용하면 짧은 causal window를
놓칠 수 있으므로 오늘 watch DB 변경을 관찰하는 read-only one-shot을 배치했다.

- launchd label: `ai.trading-agent.us-day-preflight-20260722`
- 관찰 시작: 2026-07-22 09:30 EDT
- 신규 entry 관찰 종료: 2026-07-22 15:30 EDT
- clean runtime:
  `/private/tmp/trading-agent-us-day-finalizer-20260722-d0f03cf`
- watch DB:
  `outputs/live_sessions/20260722/paper_recommendations.sqlite3`
- execution DB: `outputs/paper_execution/paper_execution.sqlite3`

## 실행 계약

1. 정규장 전에는 broker 요청 없이 대기한다.
2. watch DB가 생성되고 mtime 또는 크기가 바뀐 경우에만 기존 `preflight` CLI를
   한 번 실행한다.
3. current-bar setup이 없으면 source validation에서 차단되고 다음 DB 변경을
   기다린다.
4. 유일한 causal current setup이 있으면 실제 Alpaca Paper GET/WSS readiness와
   broker/shadow 대사를 수행한다.
5. `result=ready`를 한 번 관찰하면 증거 로그를 남기고 종료한다.
6. entry cutoff까지 ready setup이 없으면 `no_ready_current_setup` censored 로그를
   남기고 종료한다.

observer는 arm DB, delivery DB, terminal, 주문 client를 입력으로 받지 않는다.
Paper entry, OCO, cancel, flatten 또는 Telegram delivery를 수행할 권한이 없다.

## 배치 전 검증

- wrapper mode: `700`
- `zsh -n`: exit 0
- dry-run: exit 0, `broker_mutation=false`, command `preflight`
- arm, POST, DELETE, delivery, finalize, Alpaca order 호출 없음
- detached runtime HEAD: exact `d0f03cf`, clean
- `preflight --help`: exit 0
- launchd state: running, one-shot wrapper 대기 중
- 기존 ORB watch, close finalizer, Hermes projector, delivery worker: 모두 running
- 실제 Paper mutation events: 0
- 실제 broker order events: 0

## 장중 확인 항목

- `us_day_preflight_observer.events.log`의 distinct redacted 결과를 확인한다.
- ready가 있으면 같은 시각의 recommendation, candidate input, latest completed bar와
  DB source identity를 보존한다.
- blocked만 있으면 threshold를 낮추거나 setup을 만들지 않고 이유를 보존한다.
- ready는 주문 승인이나 Paper lifecycle 완료가 아니라 current setup과 계좌 대사가
  동시에 통과했다는 read-only 증거다.

## 장중 장애와 복구

10:16 EDT 점검에서 최초 wrapper가 zsh 예약 변수 `status`에 종료 코드를 대입해
반복 종료한 사실을 확인했다. ignored 운영 wrapper의 변수명을 `exit_code`로 바꾸고
해당 preflight LaunchAgent만 재기동했다.

- 수정 뒤 `zsh -n`과 dry-run은 exit 0이다.
- 재기동 뒤 observer는 running 상태를 유지했고 예약 변수 오류는 더 늘지 않았다.
- 실제 read-only preflight는 `invalid_current_orb_source`로 차단됐다. 당시 현재 세션
  actionable ORB 추천이 0건이어서 실행 계약 3의 정상 fail-closed 결과다.
- observer는 이후 watch DB 변경을 계속 관찰한다.
- arm 소비와 Paper POST/DELETE는 수행하지 않았고 실제 Paper mutation은 0건이다.

이 체크포인트는 observer 배치 증거이며 실제 자연 setup 또는 Paper POST 증거가
아니다. M2는 여전히 운영 미완료다.
