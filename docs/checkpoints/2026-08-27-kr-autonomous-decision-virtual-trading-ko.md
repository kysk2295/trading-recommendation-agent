# KR Autonomous Decision and Virtual Trading 운영 체크포인트

Task 1–7 구현 및 v14 배포 기준 SHA는
`34dc2e903981a0bb4eb7493deadb59790730ee96`이다. 이 체크포인트는 한국 주식의
읽기 전용 사회관계망·브라우저 연구, KIS 시세 확인, 추천 또는 명시적 no-trade,
내부 가상 포지션을 위한 12.2 수직 경로를 기록한다. fixture·replay·가상 결과를
수익이나 실제 체결 성과로 주장하지 않는다.

## 구현 계보

| 범위 | 커밋 |
| --- | --- |
| Task 1: KR 사회 신호 정규화와 append-only store | `e23dda29` |
| Task 2: 현재 세션 KIS GET-only corroboration | `8d938454` |
| Task 3: 결정론적 추천/no-trade/Critic artifact | `364df823` |
| Task 4: role-scoped KR 자율 도구 | `b8501862` |
| Task 5: restart-safe 내부 가상 포지션 | `7c1bea81` |
| Task 6: schema v4 지속 서비스 | `aa8bb21f` |
| Task 7: 실제 Supervisor-selected 수직 fixture | `6e5d2e9dffdf5bd347d3c7165f79a1e3e111206d` |
| 운영 결함: Hermes writer 락 충돌 생존 | `3ddf67b68eed0f93c9a7e4894f8aac9bb32fc0c0` |
| 운영 결함: reasoning prompt 비용 상한 | `f360dbd3977232bb2c3dcbc3f1b953ddf24b21ba` |
| 최종 prompt: 오래된 상세 payload 압축 | `34dc2e903981a0bb4eb7493deadb59790730ee96` |

## 설치된 v14 계약

| 항목 | 실제 관측값 |
| --- | --- |
| Research config | schema v4, SHA-256 `1c9afdb00b94d9c9ca1d26c1e98fbd3196791fce2cd11c444b1a16aaa4152585` |
| Research plist | `ai.trading-agent.research-agent-runtime-v14.plist`, SHA-256 `c5efece7a36aea9ce9358003345f4615993fc1a07fb368f0de9d62d476804d09` |
| Research LaunchAgent | label `ai.trading-agent.research-agent-runtime`, PID `64462`, runs `3`, 마지막 exit `143`은 최종 SHA를 적재한 명시적 kickstart |
| Browser Gateway | label `ai.trading-agent.local-browser-gateway`, PID `76788`, runs `3`, last exit `0` |
| Health | `2026-08-27T08:53:27.707732Z`, `ready/runtime_ready`, config digest 일치 |
| 최종 업무 report | `2026-08-27T08:54:42.782169Z`, broker mutation `0`, trading mutation `0` |

config, plist, health, KIS credential 파일은 모두 현재 사용자 소유 mode `600`이다.
v13 config/plist는 rollback artifact로 보존했고, v14 output root는 별도로 격리했다.
정식 최종 프로세스는 보고서 갱신 뒤에도 같은 PID와 runs를 유지했다.

## 실제 지속 실행에서 확인한 동작

v14는 이전 v1 episode와 lineage-linked v2 episode를 보존했다. v2는 예약 wake에서
모델이 직접 `loop_engineer` 역할 위임을 선택했고, task state/owner/decision/delegate가
append-only 스텝으로 남았다. 이는 Supervisor가 브라우저→KIS→Critic 순서를 고정한 것이
아니라 모델이 역할과 도구를 선택한다는 실제 운영 증거다.

초기 배포에서는 두 운영 결함을 실제로 재현하고 수정했다.

1. Hermes delivery integration PID `25988`과 Research OS가 같은 delivery DB의 비차단
   writer lease를 경쟁했다. `HermesDeliveryWriterLeaseUnavailableError`가 영구 루프 밖으로
   나가 exit 1 재시작을 반복했다. 수정 전 foreground 실행은 한 틱 안에 종료됐고, 수정 후
   동일 경쟁 PID가 존재한 상태에서 2분 이상 여러 틱을 통과하며 종료되지 않았다.
2. 누적 v2 prompt 23,578바이트는 Claude CLI의 `error_max_budget_usd`를 발생시켰고 실제
   메타데이터 비용은 USD `0.05139`였다. 비용 권한을 높이지 않고 반복 authority 필드와 오래된
   상세 payload만 compact rendering으로 바꿨다. v2 prompt는 15,614바이트에서 valid
   `task.history` tool call을, 13-step Loop Engineer prompt는 13,396바이트에서 valid `defer`를
   반환했다. typed request와 task DB에는 최대 32-step 원본 계보가 그대로 남는다.

재시작 전후 v2 task ID는
`054d9f431dd1dc463c288d54f87b25298045d7b29bd5f13584767a2241321a06`으로 동일했고,
signal/recommendation/no-trade/position replay 중복은 0이었다. 현재 task는 두 번째 과거
reasoning 실패의 60분 backoff를 보존한 `blocked` 상태이며 다음 wake는
`2026-08-27T09:47:31.523800Z`이다. 이 backoff를 운영 검증을 위해 조작하지 않았다.

## 브라우저·KIS·가상체결 경계

- 운영 v1 task에는 실제 Gateway `browser.open` 결정과 330-byte bounded observation,
  content/call digest가 남았다. 최종 v14 browser social-evidence DB에는 승격된 자연 신호가
  없어서 row count는 `0`이다.
- 장 종료 뒤 자연 사회 신호가 없었으므로 KIS market receipt, KR social signal, recommendation,
  explicit no-trade, open/terminal virtual position은 모두 `0`이다. 임의 종목이나 setup을
  만들어 이 수를 채우지 않았다.
- fixture 수직 경로는 사회 신호 cluster 정규화, 정확히 3개 검토된 KIS GET contract,
  모델 선택/위임, Critic 승인 또는 no-trade, future completed-bar 체결, same-bar stop 우선,
  재시작 exact replay를 검증한다. fixture 가상 결과는 실제 체결이나 수익이 아니다.
- KIS/LS mutation, Alpaca 호출, 한국 주문·계좌·잔고 mutation은 운영과 fixture 모두 `0`이다.
  KIS 계정 파일은 승인된 mode-600 경로에서만 읽으며, 이번 장 종료 QA에서는 provider 호출을
  억지로 만들지 않았다.

## 자동 및 수동 검증

- Task 7 vertical: `10 passed`; 두 모델 선택 순서, six KR tools, recommendation/no-trade,
  stop-first terminal outcome, exact restart replay를 포함한다.
- 최종 reasoning/실행/복구/vertical 집중 회귀: `46 passed`.
- Research OS 락 복구와 schema v4 서비스 집중 회귀: `18 passed`.
- 자율·브라우저·KIS KR·KR 전체 관련 회귀: `1386 passed`; 변경 전 SHA에서도 동일했던
  `test_child_import_closure_excludes_operational_authority` 1건만 실패했다.
- whole-suite 첫 dashboard publisher 실패도 변경 전 `8d938454` 깨끗한 기준에서 동일하게
  재현했다. 관련 수직 회귀와 분리된 선행 결함이다.
- 최종 변경 파일 Ruff format/check 통과, basedpyright `0 errors, 0 warnings, 0 notes`,
  공식 no-excuse `0`건, `git diff --check` 통과.
- CLI help exit `0`이고 8개 명령을 노출한다. 존재하지 않는 config/plist는 exit `2`와
  stdout/stderr `0`바이트다. 실제 v14 status는 family `6`, model calls `0`, broker mutation
  `0`이며 durable model failures 때문에 role status를 `failed`로 정직하게 표시한다.
- status/health/task payload의 `authorization`, API key/secret/token, cookie, full HTML,
  account ID 패턴 검사는 `0`건이다.

## 완료 범위와 남은 정규장 게이트

Task 1–7 코드, fixture 수직 경로, exact-SHA push, v14 설치, restart/no-duplicate,
Hermes 락 충돌 생존, Claude 예산 내 자율 역할 선택까지 완료했다. 실제 운영에서 자연 신호가
없고 한국장이 종료된 상태였으므로 **실제 현재 세션 KIS corroboration이 결합된 recommendation
또는 explicit no-trade artifact**는 아직 관측하지 못했다. 이는 구현 누락을 성공으로 축소하지
않기 위한 명시적 잔여 게이트다.

다음 KRX 정규장에서 v14가 자연 browser evidence를 찾으면 동일 task lineage로 KIS GET-only
corroboration과 recommendation/no-trade 중 하나를 append해야 한다. 그때도 추천에는 timestamp,
entry, stop, targets, quantity, rationale, counterevidence와 browser/KIS/Critic lineage가 모두
있어야 하며, 없으면 no-trade와 future wake를 남겨야 한다. 그 관측 전에는 실제 한국 추천이나
가상 성과를 주장하지 않는다.
