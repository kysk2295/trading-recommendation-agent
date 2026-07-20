# KR theme day open-session smoke attestation 체크포인트

## 목적

기존 `run_kr_theme_day_session_verify.py`는 session source integrity를 query-only로 검증하지만 일부 phase 완료나 fixture manifest도 정상 검증할 수 있다. 이를 실제 열린 KRX GET 실증이나 scheduler 배포 승인으로 확대하지 않기 위해 별도 production-manifest-only attestation을 둔다.

## 승인 계약

`run_kr_theme_day_open_smoke_verify.py`는 다음을 모두 만족할 때만 evidence를 만든다.

1. exact onboarding receipt와 schema v2 session manifest
2. intraday/EOD fixture path가 모두 없는 production manifest
3. official open-day calendar와 현재 source가 일치하는 session verification
4. KST 09:01 이상 15:30 미만인 최초 검증시각
5. 최신 `register`, `start`와 phase별 전체 최신 sequence인 `intraday_collect`, `intraday_entry`, `intraday_exit`가 순서대로 completed
6. 다섯 event 각각과 exact 결합된 source attestation 하나
7. 장중 세 event가 같은 현재 minute에 호출되고 현재 전체 source-state와 검증시각까지의 인과적 source-state가 같음

일반 verifier의 completed count만으로는 이 계약을 만족할 수 없다. fixture, 장외 최초 실행, 불완전 phase, source drift와 artifact tamper는 evidence 생성 전에 차단한다.

## Evidence와 재시작

schema v1 evidence는 session/date/minute, 세 phase event ID와 세 source attestation ID를 content-address하고 mode-600 immutable JSON으로 게시한다. 보고서에는 symbol, session/evidence ID, 가격과 경로를 기록하지 않는다.

event `observed_at`은 기존 supervisor가 child를 호출한 시각이고 source attestation은 child 종료 뒤 생성한다. 따라서 receipt는 event보다 늦을 수 있지만 evidence 검증시각을 넘을 수 없다. 최초 생성은 CLI의 실제 clock만 사용하며 time override가 없다. final 경로 대신 private pending artifact를 먼저 게시하고 같은 source에서 evidence를 다시 계산한 뒤 일치할 때만 final을 게시한다. final은 현재 호출이 소유한 pending inode를 no-overwrite hard link로 승격한다. alias publisher는 자신이 link를 만든 뒤 실패한 경우에만 내부에서 final을 제거하고, caller는 소유권을 알 수 없는 publish 예외의 final을 지우지 않는다. cleanup ownership은 device/inode뿐 아니라 size·mtime·ctime·link count 전체와 no-follow parent dirfd를 요구하므로 같은 inode를 다시 연결하거나 parent를 symlink로 교체한 foreign final을 삭제하지 않는다. 이미 evidence가 있으면 publication lock이나 evidence writer를 만들거나 열지 않고 query-only evidence·manifest·receipt·Opportunity를 읽는다. experiment ledger는 mode-600/current-owner/single-link 파일과 mode-700 parent identity를 descriptor에 고정하고 한 번의 SQLite read-only backup으로 만든 메모리 snapshot에서 모든 onboarding 및 source-state 조회를 수행한다. main DB와 WAL/SHM identity drift를 모두 차단한다. 저장된 장중 검증시각으로 calendar, audit, attestation 및 현재 source를 다시 대사하므로 폐장 뒤 exact replay는 가능하지만 다른 payload나 같은 minute source drift는 기존 evidence로 덮지 못한다.

이 evidence는 전용 OS identity와 mode-700 root 신뢰경계 안에서 local source를 재생했다는 증거다. 원격 provider 서명이나 network 호출 증명은 아니며 동일 UID 임의 코드가 source와 attestation을 함께 위조하는 host compromise는 범위 밖이다. 따라서 실제 열린 KRX GET 운영 체크포인트의 요청 수·수신 시각 확인도 별도 필수이고, local evidence 하나만으로 scheduler를 승인하지 않는다.

## 검증 상태

- production-shaped local stores: production manifest, started trial, 세 raw receipt, 다섯 phase event/source attestation으로 first/replay 통과
- fixture manifest, 15:30 최초 생성, 불완전 current-minute phase: 차단
- current-minute cycle과 다른 event 시각, 다른 cycle의 더 최신 sequence, future source와 검증 뒤 source drift: 차단
- register/start 누락 또는 다섯 phase sequence 역전: 차단
- future EOD/post-session event와 검증시각보다 미래인 전체 history event: 차단
- forged event/attestation content-address와 publish 중 source drift: 차단
- casefold/Unicode-normalized report·evidence·pending이 파생 attestation store를 포함한 session source file과 같거나 그 하위인 경로: write 전 차단
- evidence symlink loop: report가 manifest와 alias인 경우 manifest 보존, 안전한 report에서만 traceback·경로 없는 blocked report
- onboarding exact replay: manifest·receipt·Opportunity publication lock/writer와 `chmod`/`fchmod` 호출 `0`; non-mode-700 parent는 metadata 변경 없이 차단
- existing evidence replay: evidence publication lock/writer와 재게시 `0`
- report directory swap: no-follow retained dirfd publication으로 protected manifest 보존
- experiment ledger symlink/hard-link, main DB 또는 WAL/SHM snapshot drift와 onboarding 이후 source-state 전 ledger swap: 차단
- final publisher post-link failure: publisher 내부에서 자신의 exact metadata final만 제거; 다른 inode, same-inode relink와 parent-swap foreign final 보존
- commit ordering: final link·content/path/link-count 검증과 parent `fsync`를 source가 남은 invalid two-link 상태에서 모두 끝내고 source unlink를 마지막 결과 결정 syscall로 실행
- pre-commit cleanup 실패: source와 final을 two-link 상태로 유지해 정상 evidence reader가 차단; commit 뒤 descriptor close 실패는 성공 결과를 뒤집지 않음
- success report 기록 실패: 현재 호출이 만든 신규 evidence 제거, 기존 replay evidence 보존
- content tamper: 차단
- actual CLI help: fixture/provider/credential/account/order/endpoint/time override 옵션 `0`
- missing manifest: exit `1`, evidence `0`, redacted blocked report
- focused open-smoke/session/query-only: `59 passed in 6.98s`
- related KR theme/same-cycle: `283 passed, 2564 deselected in 16.54s`
- full suite: `2847 passed in 152.48s`
- 실제 실행 시각: 2026-07-20 10시대 KST, KRX 장중이지만 작업공간에 당일 장 전 등록된 production manifest가 없어 actual smoke는 차단
- 실제 provider GET, production open-smoke evidence와 국내 account/order mutation: `0`

## 다음 단계

실제 열린 KRX session에서 새 production manifest의 `tick`을 한 cycle 실행하고 일반 verifier 뒤 이 CLI로 durable evidence를 만든다. raw receipt 3종, phase event 5개와 source attestation 5개가 exact인지 대사한 뒤에만 같은 경로를 읽는 최소권한 launchd와 restart soak를 추가한다.
