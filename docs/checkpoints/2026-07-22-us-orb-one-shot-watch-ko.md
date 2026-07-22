# US ORB one-shot regular-session watch 체크포인트

날짜: 2026-07-22

## 운영 판정

- 오늘 preregistered ORB `shadow_forward` trial을 어제 등록돼 오늘 effective인 clean detached runtime
  `be0cde782072afa61f79b39ac810e4eece350143`으로 실행하도록 배치했다.
- 현재 시점은 NYSE premarket이며 one-shot watch는 단일 process, 단일 run으로 정규장 open을 기다리고 있다.
- watch는 5분 간격의 KIS 미국시장 ranking과 NYSE halt GET-only 수집을 수행한 뒤, 정규장 안에서
  trial start와 ORB read-only scan을 실행하고 장후 exact terminal chain을 시도한다.
- wrapper는 child 종료 상태를 받은 뒤 자신의 launchd label을 제거하므로 정상 종료 후 keepalive 재시작하지 않는다.
- 현재 trial은 1건, event는 0건이고 Paper execution 원장의 order intent, broker event,
  mutation intent, mutation event는 모두 0건이다.
- Paper arm은 만들거나 확인하지 않았고 계좌·주문 mutation 또는 Alpaca live endpoint는 사용하지 않았다.

## 장전 실제 관찰

- watch registration은 기존 exact trial replay로 끝나 새 trial을 만들지 않았다.
- 장전 GET cycle은 ranking 실패 없이 완료됐고 premarket cycle audit이 누적되고 있다.
- premarket ranking, risk, request coverage, registration audit와 watch audit은 모두 mode `600`이다.
- stdout·stderr는 mode `600`, 비밀 없는 one-shot runner는 mode `700`이다.
- 공유 checkout의 다른 세션 미커밋 변경은 실행 runtime이나 커밋에 포함하지 않았다.

## 실제 수직에서 발견한 권한 결함

- 오래된 `paper_execution.sqlite3`, WAL과 SHM이 mode `644`로 남아 있었다.
- `ExecutionStore.writer()`가 main DB를 SQLite open 전에 `O_NOFOLLOW` descriptor로 mode `600` 생성·교정하고,
  prepare 뒤 WAL과 SHM까지 mode `600`으로 교정하도록 수정했다.
- watch와 trial child가 공유하는 `append_cycle_audit()`도 일반 append 대신 owner-only append 경계를 사용한다.
- 실제 Paper execution main/WAL/SHM/lock과 오늘 registration/watch audit 파일은 내용 변경 없이 mode `600`으로 교정했다.

## 검증

- 권한 회귀 RED: 신규·기존 execution main/WAL/SHM이 `(644, 644, 644)`로 실패
- audit 권한 회귀 RED: 신규 cycle audit이 `644`로 실패
- 집중 테스트: 21 passed
- 전체 테스트: 3292 passed
- Ruff 전체: 통과
- basedpyright 전체: 0 errors, 0 warnings
- Python no-excuse: 통과
- 수동 store QA: 신규 및 재진입 main/WAL/SHM/lock 모두 mode `600`
- clean runtime 상태: detached HEAD, tracked 변경 없음

## 아직 남은 오늘 세션 증거

- NYSE regular open 뒤 trial `started` event와 current completed-minute scan을 확인해야 한다.
- 자연 ORB setup이 없으면 threshold를 바꾸지 않고 장후 `censored_no_setup`으로 확정한다.
- 자연 setup이 있더라도 명시적 owner arm이 없으므로 이 watch는 Paper POST를 수행하지 않는다.
- 장후 metrics, daily research, lane snapshot, independent Reviewer, trial terminal과 Hermes 결과를 대사해야 한다.
