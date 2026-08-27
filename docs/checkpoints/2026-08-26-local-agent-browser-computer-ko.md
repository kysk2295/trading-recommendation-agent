# Local Agent Browser Computer 운영 체크포인트

구현 기준은 `main`의 `2320903dff2e79f8ffec8efe05cdf465997c3e6f`이다. 이 체크포인트는
로컬 Mac의 실제 `launchd`와 전용 Chrome을 사용한 읽기 전용 브라우저 컴퓨터 및 schema v3
Research Agent 배포를 기록한다. 수익성, 한국 종목 추천, 가상 체결 또는 실거래 성과를 주장하지
않는다.

## 설치된 운영 계약

| 항목 | 관측값 |
| --- | --- |
| Gateway config | `local-browser-gateway-v1.json`, SHA-256 `41fa08b69e2e59086ed8e636b757d03f7cefbcad18f276d104047d7d9684c478` |
| Gateway plist | `ai.trading-agent.local-browser-gateway-v1.plist`, SHA-256 `86d8b12bb038894fa42789fbd36843a870ad92f2d28aa98c58bc98e34e48ad01` |
| Gateway label | `ai.trading-agent.local-browser-gateway` |
| Research config | schema v3, SHA-256 `c65f28d0395d7544a5b4a40b43a11a9e144e75683e1193025f318aeef49551e3` |
| Research plist | `ai.trading-agent.research-agent-runtime-v13.plist`, SHA-256 `bc5fa0cb2ec0b660fc35ebca70292feeed5bbc7882f0eefdf27d69d1be733017` |
| Research label | `ai.trading-agent.research-agent-runtime` |

네 파일은 모두 현재 사용자 소유 mode `600`이다. Gateway socket과 receipt DB도 현재 사용자
소유 mode `600`이며, screenshot은 digest 이름의 mode `600` PNG로만 저장됐다. Gateway
LaunchAgent는 프로젝트 root를 `WorkingDirectory`로 사용한다. 첫 실제 배포에서 이 키가 없어
launchd가 `/`에서 `uv`를 실행하고 `pydantic` import 전에 종료하는 결함을 재현한 뒤
`7c2ed2884989e00c3b1ead4bf51d77c8bd9d7c63`에서 수정했다.

## 실제 Chrome 및 Gateway QA

실제 전용 Chrome에서 `status`, public HTTPS `open`, `read`, `capture`, `search`, `follow`를
실행했다. `status`는 `ready=true`, `active_page_count=1`을 반환했다. `open`, `read`,
`capture`, `search`는 성공했고, 한 `follow`는 우회하지 않고
`browser_navigation_blocked`를 반환했다.

- open receipt: `7034fca9127ad351122062a4e1f878256d38946dc043ac23e77218113ebcff1f`
- read receipt: `faba08eb9b0869183af915c5c5b02b40055bb37feb49e337e98e0f1b9eb5fa69`
- read observation: `6d42974b4c16343e457e60abd8e057caa87c2f4438e0d2cb9aa69ea39f79fdc9`
- capture receipt: `bd798152a7f0a1fcff20f1d7fdb87ce4b64d1594d7447ad30e80a8b4491a614f`
- screenshot digest: `ab9a04d071f7991f6c2ddd694b349f83b235f9710ac99c5a37858e03e515a108`
- search receipt: `d1a26f098e45a28f706346ca50a2197055bfe8eed169beb3bc349f7f37e409d7`
- honest blocked follow receipt: `72283f97a10c2db70364ec5e11270d875955af799087f44e2a31724cb835b430`

receipt DB는 `PRAGMA quick_check=ok`이고 실제 Chrome QA 직후 action/response 10쌍, 최종
운영 재확인 시점에는 14쌍이 각각 일치했다. 전체 14쌍의 민감정보 패턴 검사 결과는 0건이었다.
cookie, header, token, credential, account 식별자, 전체 HTML 또는 raw authentication response는
저장하지 않았다.
HTTP, localhost, `file:` 입력은 Chrome 이동과 receipt 기록 전에 거부됐고, 이후 `status`도 계속
ready였다.

Gateway를 `launchctl kickstart -k`로 두 차례 실제 재시작했고 최종 PID가 `76788`,
`runs=3`, `last exit code=0`, PPID 1, ready 상태를 회복했다. 재시작 전후 동일 open
request의 응답은 같았고 open receipt 수는 1로 유지돼 replay가 중복 기록을 만들지 않았다.

## schema v3 Research Agent 운영 전환

v11은 schema v2 rollback authority로 보존했다. 잘못된 v12 후보는 v11 output root를 공유해
health digest가 충돌했으므로 활성화하지 않았고, config/plist를 각각
`.rejected-shared-output` 이름으로 복구 가능하게 보존했다. v13은 동일 durable cycle DB를
사용하되 output root를 격리하고 검증된 Gateway config를 명시적으로 결합한다.

첫 v13 교체는 정상 프로세스도 첫 120초 업무 틱이 끝나기 전에는 health를 쓰지 않아 30초
교체 gate에서 롤백되는 순서 결함을 드러냈다. `1bc958d4808dd83e241a4e9be306f770406feee7`은
runtime, config, lease, browser binding 초기화가 성공한 직후 fresh readiness를 쓰고 업무
완료 report는 기존 경로로 유지한다. 이후 v11→v13 교체는 exit 0이었고, launchd는 v13 plist,
PPID 1의 실행 프로세스, matching config health를 관측했다.

실제 schema v3 틱은 정확히 한 개의 지속형 `market_context` / `kr_equities` episode
`473e028960c14c1cea6498d03127d39335b9f50357db34f60b3c29f5b207fd17`을 만들었다. 모델이
선택한 무기한 `waiting_event`는 외부 producer가 없어 연구를 멈추므로
`0cf93ffd521f5e68c3ab95e77ba9bb0a7376d0d8`에서 모델 결정을 이력에 보존한 채 10분 periodic
review로 전환했다. Research Agent 재시작에서 같은 task ID와 기존 3단계가 유지되고 4번째
`waiting_time` 단계가 추가됐다.

첫 periodic wake에서 모델은 스스로 `browser.status`를 선택했지만, 기존 reasoning prompt가
도구 이름만 주고 인자 계약을 주지 않아 허용되지 않은 `check` 인자를 생성했다. 권한 경계는
Gateway 호출과 receipt 기록 전에 이를 거부했다. 직접 호출과 실제 spawn worker에서 올바른
빈 인자 호출이 성공함을 확인한 뒤, `cae76a738050fb3e6d2ab2a2b748ecb2f5903cd7`에서 prompt
schema v2에 정확한 role-scoped 서명(`browser.status()`, `browser.search(query)` 등)을
추가하고 이름/서명 불일치를 거부했다. 수정 코드 재시작은 PID `70242`에서 `75612`로
바뀌었고 기존 task와 retry lineage를 유지했다.

새 코드의 첫 scheduled retry에서는 격리 reasoner가 한 번 비정상 종료해 launchd가 exit 1을
관측하고 자동 재시작했으며, task는 `autonomous_reasoning_failed`와 다음 retry 시각을 append-only
이력에 남겼다. Research Agent만 잠시 중지한 뒤 동일한 17.5KB prompt를 직접 재검증한 결과
17초 안에 유효한 `browser.search(query)` 선택을 반환했다. query 원문은 출력하거나 기록하지
않았다. 이를 provider/worker의 일시 실패로 분류하고 backoff 이력은 조작하지 않았으며, v13을
activation exit 0, PID `81120`, PPID 1로 즉시 복구했다.

후속 scheduled retry에서 Claude CLI는 exit 0이었지만 `defer` variant의 필수 wake를 생성하지
않았다. 인증·예산·rate-limit 장애가 아니라, Claude가 금지하는 top-level discriminated union을
평탄화하면서 variant별 `required`와 Pydantic cross-field 제약을 버린 변환 결함이었다.
`2320903dff2e79f8ffec8efe05cdf465997c3e6f`은 top-level을 required `response` envelope로
바꾸고 원래 variant `$defs`와 `oneOf`를 보존하며, `AutonomousDefer`에는 시간 또는 이벤트 중
정확히 하나의 non-null wake를 요구한다. 540 pure LOC였던 provider 파일은 class identity와
기존 facade import를 유지한 채 역할별 250 LOC 이하 모듈로 분리했다.

동일 운영 task, 8개 prior step, 10개 허용 도구의 실제 Claude 요청을 Research Agent만 중지한
상태에서 다시 실행했다. 22.588초 안에 Pydantic 검증을 통과한 `defer`가 반환됐고 wake 수는
정확히 1이었다. prompt와 raw 응답은 출력하지 않았다. 이어 운영 Gateway에 공개 한국시장
검색·open·read를 수행해 browser social-evidence DB가 0건에서 1건으로 증가함을 관측했다.
이 1건은 실제 Chrome/운영 DB 수직 경로의 수동 smoke evidence이며, scheduled agent가 선택한
자율 연구 결과라고 주장하지 않는다.

배포 후 launchd는 PID `90252`, `state=running`, `runs=1`, `last exit code=(never exited)`이다.
지속형 KR agenda episode는 같은 task ID 하나와 append-only step 8개를 보존하며, 마지막 실패
backoff도 조작하지 않아 다음 자연 재시도는 `2026-08-27T03:51:05.367252Z`이다. 현재 task
상태는 `blocked`, broker mutation은 0이다.

## 자동·수동 검증

- 변경 범위 회귀: `450 passed in 24.32s`.
- readiness/health 집중 회귀: `37 passed`.
- periodic agenda와 browser builder 집중 회귀: `33 passed`.
- tool signature, reasoning, spawn worker 집중 회귀: `55 passed`.
- terminal episode successor 및 재시작 replay 회귀:
  `test_terminal_episode_creates_a_lineage_linked_successor`,
  `test_restart_after_terminal_replays_one_successor` 통과.
- Ruff format/check: 변경 Python 94개 통과.
- basedpyright: `0 errors, 0 warnings, 0 notes`.
- 공식 no-excuse 검사: `no violations in 94 file(s)`; 모든 변경 Python은 pure LOC 250 이하.
- Gateway CLI: help exit 0, missing config exit 2와 빈 stdout/stderr, 실제 status exit 0/ready.
- Research CLI: help exit 0, missing config/plist exit 2와 빈 stdout/stderr, 실제 v13 status exit 0,
  family 6, `broker_mutation=0`.
- `git diff --check`: 통과.
- Claude schema 보완 집중 회귀: `76 passed in 12.69s`.
- 보완 변경 8개 파일 Ruff format/check 통과, basedpyright `0 errors`, 공식 no-excuse 0건.
- 전체 suite: `6454 passed`, `34 failed`; 대표 실패 7개를 변경 전 기준 SHA의 깨끗한 임시
  worktree에서 동일하게 재현해 dashboard 기본 config, ledger v9 fixture, import closure,
  US close replay 등의 선행 결함으로 분리했다.

## 보존된 안전 경계와 다음 범위

이번 subproject는 browser/evidence 기반 연구 컴퓨터만 배포한다. KIS/LS mutation, Alpaca 요청,
한국 추천·진입·손절·목표가, 한국 가상 체결·포지션, Loop Engineer 승격은 만들거나 실행하지
않았다. broker/trading mutation은 0이다. DNS rebinding 방어는 승인된 계획의 명시적 후속
항목으로 남아 있다.

다음 승인 범위는 별도 계획인 **12.2 KR Autonomous Decision and Virtual Trading**이다. 그
단계에서만 현재 세션 KIS read-only 가격 truth, timestamp·entry·stop·targets를 갖춘 추천,
한국 가상 체결, same-bar stop 우선, immutable outcome, Dashboard/Hermes 표시와 outcome 기반
학습을 이 browser evidence에 연결한다.
