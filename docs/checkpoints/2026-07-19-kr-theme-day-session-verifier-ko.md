# KR theme day session query-only verifier 체크포인트

## 목적

`run_kr_theme_day_session_verify.py`는 운영 tick을 다시 실행하지 않고 한 session의 manifest, official calendar, phase audit, source attestation과 현재 source store를 독립적으로 대사한다. 실제 열린 KRX smoke에서 “process가 exit 0이었다”가 아니라 어떤 source identity가 최신 완료를 지지하는지 판정하는 표면이다.

## As-of source

과거 minute attestation은 이후 정상 append 때문에 바뀌면 안 된다.

- intraday entry reference는 cycle 마지막 microsecond 이하 `filled_at`만 포함
- intraday exit reference는 같은 cutoff 이하 `evaluated_at`만 포함
- EOD entry/exit는 session 15:31 cutoff
- post-session lifecycle은 exact `decision_session_date`

fixture에서 09:04 phase를 attestation한 뒤 실제 09:05 shadow entry를 추가해도 09:04 source-state SHA-256과 reference count는 동일했다. 같은 09:04 cycle에 fault-injection entry를 추가하면 digest가 달라져 verifier가 차단했다.

## 최신 Attempt 판정

audit와 attestation의 모든 링크는 구조적으로 검증한다. readiness는 각 `(phase, cycle)`에서 sequence가 가장 큰 attempt만 권위로 사용한다.

- 최신 completed: event-bound attestation 하나와 current source digest/count exact 필요
- 최신 blocked: integrity가 유효해도 operational readiness는 blocked
- legacy completed 뒤 새 attested replay: 새 attempt가 권위
- orphan/mismatched attestation, missing calendar/source, store tamper: fail-closed

보고서는 전체 event 수를 노출하지 않고 verified completed/latest blocked count만 남긴다. manifest/session ID, digest, symbol, price, path와 raw payload는 기록하지 않는다.

## 검증

- related KR session verifier/children: `37 passed`
- 전체 회귀: `2759 passed`
- Ruff와 format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual CLI help: exit `0`, provider/credential/account/order/endpoint 입력 `0`
- missing manifest: exit `1`, blocked report mode `600`
- provider network와 국내 account/order mutation: `0`

## 다음 단계

열린 KRX session에서 production manifest의 `tick`과 이 verifier를 연속 실행해 KIS raw receipt, phase event와 attestation count가 exact인지 확인한다. 그 실제 smoke evidence가 통과한 뒤 같은 manifest/audit/evidence/report 경로를 보존하는 최소권한 launchd plist와 restart soak를 추가한다.
