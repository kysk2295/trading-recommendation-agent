# Alpaca SIP entitlement admission actual 체크포인트

## 제품 경계

실제 SIP WebSocket 연결 실패를 단순 stderr가 아니라 다음 연결 전 machine-readable
admission evidence로 바꾸는 `run_alpaca_sip_entitlement_admission.py`를 추가했다.

```text
private append-only stream SQLite
-> query-only failed-attempt / terminal-session replay
-> latest exact evidence selection
-> ready | blocked | unknown
-> content-addressed mode-600 artifact + private report
```

- 최신 bounded-complete terminal은 `ready/bounded_complete`, exit `0`이다.
- 최신 공식 provider code 409는 `blocked/insufficient_subscription`, exit `2`다.
- 402 인증 실패, 406 connection limit, transport·handshake·protocol failure와 증거
  부재는 entitlement를 추정하지 않고 `unknown`, exit `1`이다.
- 같은 시각에 terminal과 attempt가 함께 있으면 실패 attempt를 최신으로 취급해
  fail-closed한다.
- artifact는 exact attempt 또는 terminal content SHA를 보존한다.
- CLI에는 credential, provider client, account, position 또는 order import가 없다.

## TDD와 검증

- missing CLI RED 뒤 observable CLI failure: `3 failed`
- GREEN focused: entitlement admission + attempt/store `14 passed`
- 전체 pytest: `3488 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- changed-file Ruff format/no-excuse: pass / 위반 `0`
- pure LOC: admission `115`, artifact `233`, CLI `127`, E2E `218`

수동 CLI QA는 다음 결과였다.

- `--help`: exit `0`
- malformed date: exit `1`, `invalid/evidence_validation_failed`
- missing evidence: exit `1`, `unknown/transient_or_missing_evidence`
- bounded-complete fixture: exit `0`, `ready/bounded_complete`
- fixture artifact/report: mode `600`

## exact-SHA actual evidence

구현 commit
`00af3d336f7aca0a774eecf6cefe85f12b7179cd`에서 2026-07-23 09:35 EDT에
보존된 actual AAPL SIP stream DB를 query-only로 투영했다.

actual 연결 원장에는 다음 terminal evidence가 있었다.

- failed at: `2026-07-23T13:35:02.892607Z`
- stage: `authentication_control`
- provider failure: `insufficient_subscription`
- data link / terminal session: `0 / 0`

admission 결과는 다음과 같다.

- exit: `2`
- status / reason: `blocked / insufficient_subscription`
- evidence SHA:
  `c85c6e265da8227498c2637bc6e62fd5f033709f6d82afe09791c32c0d491645`
- artifact ID:
  `662777904311eff640d792b160d95202c40f90911de50beb7d26327cd48c8770`
- artifact file SHA:
  `9c83bc67001f6c5e2ff2199e76c7cf10ac7f9c621113ebfb70afe17c8026985e`
- artifact/report mode: `600 / 600`
- output directory mode: `700`

입력 stream DB SHA는 실행 전후 모두
`999cf3a1a69b6e06d2830f9bfbfd69e402a2ce262bd93334c26e9344096eaf85`였고
mtime도 변하지 않았다. exact replay는 exit `2`, artifact `1 -> 1`,
`artifact created: false`, network access와 broker mutation `0`이었다.

## 다음 정규장 예약 교체

2026-07-24 09:35 EDT에 대기하던 기존 SIP smoke는 이 409 evidence를 읽지 않고 같은
자격증명과 WebSocket을 다시 여는 runner였다. provider 접근 전인 2026-07-24
00:48 KST에 해당 future job label만 제거하고 다음 at-most-once admission으로
교체했다.

- label: `ai.trading-agent.alpaca-sip-admission-20260724`
- 실행 시각: 2026-07-24 09:34 EDT / 22:34 KST
- frozen runtime:
  `/private/tmp/trading-agent-sip-admission-20260724-9cfe56a`
- runtime commit: `9cfe56a778afce914e825f3d3e043058c16e3044`
- expected current result: exit `2`, `blocked/insufficient_subscription`
- wrapper/log mode: `700 / 600`
- atomic claim·완료 receipt: enabled

기존 SIP smoke runner 파일과 과거 실패 원장은 삭제하지 않았다. US forward,
KR finalizer, Hermes와 장후 closeout/research job은 변경·중단·재시작하지 않았다.

## 제한과 다음 운영 규칙

이 결과는 현재 사용 가능한 자격 범위에서 SIP trade stream이 제공되지 않는다는
실제 차단 증거다. REST SIP 완료 분봉, 옵션 indicative data 또는 Paper 계좌 권한을
부정하거나 승인하는 증거가 아니다. 새 bounded-complete terminal처럼 이보다 최신인
반대 증거가 생기기 전에는 동일 조건의 SIP smoke를 반복해 성공으로 가장하지 않는다.
Paper entry·OCO·reconcile·EOD flat smoke와 Allocation Manager 권한은 열리지 않았다.
