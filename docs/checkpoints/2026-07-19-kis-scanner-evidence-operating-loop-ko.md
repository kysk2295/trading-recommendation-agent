# KIS scanner evidence operating loop 체크포인트

## 완성 범위

- 기존 KIS scan/watch research projection opt-in 경로를 그대로 사용한다.
- raw Opportunity, canonical candidate dataset과 scanner SQLite commit 뒤 evidence projection을 실행한다.
- 동일 scanner store를 query-only로 다시 검증하고 content-addressed artifact를 쓴다.
- artifact root는 projection store 부모의 `research-evidence/`로 결정한다.
- exact retry는 같은 snapshot과 artifact 하나를 재생한다.

## 실패와 권한 경계

- Opportunity 또는 research projection 설정이 없으면 기존 경로와 동일하게 no-op이다.
- scanner projection이 실패하면 evidence writer에 도달하지 않는다.
- evidence 검증·publish 실패를 성공 scan으로 축소하지 않는다.
- artifact는 candidate selection 사실만 보존하며 claim은 `unconfirmed`다.
- provider·credential·account/order endpoint와 broker mutation을 추가로 열지 않는다.

## 검증

- integration/scan/watch/scanner evidence: 31 passed
- full repository: 2326 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 수동 QA

- `run_kis_paper_scan.py --help`: exit 0
- partial research projection arguments: exit 2, credential/provider 전 차단
- fixture projection 최초·재생: same snapshot, artifact 1개, `unconfirmed` claim
- provider·credential·account/order endpoint와 broker mutation 추가 호출: 0건

## 다음 단계

- provider별 deletion cursor와 complete-history coverage
- production scanner evidence의 장기 forward 누적
- 다음 열린 NYSE 정규장의 bounded SIP GET smoke
