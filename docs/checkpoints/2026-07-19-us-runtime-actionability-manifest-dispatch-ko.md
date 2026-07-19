# US runtime actionability manifest dispatch 체크포인트

## 완료 계약

- query-only signal outbox reader가 existing mixed `trade-signals.v1.jsonl`을 structural Pydantic replay하고 symlink, non-regular/non-owner/hard-link, duplicate ID와 malformed row를 차단한다.
- runtime dispatcher는 dynamic plan이 소유한 READY feature binding마다 snapshot 시점에 current인 conditional signal을 찾는다.
- current signal 0개는 no-op이고 정확히 1개만 manifest가 되며 2개 이상은 모든 manifest write 전에 fail-closed한다.
- scan cycle identity는 base signal `observed_at`으로 고정해 한 base당 terminal 하나라는 기존 assessment 계약을 유지한다.
- manifest는 content-addressed filename으로 mode 600 immutable write되고 exact cycle replay는 new 0/replay 1이다.
- `run_us_runtime_fleet_cycle.py`는 `--conditional-signal-outbox`와 `--actionability-manifest-root`를 함께 줄 때만 READY fleet result에서 manifest를 dispatch한다.
- bounded fleet supervisor도 같은 optional pair를 매 cycle에 전달하며 한쪽만 주어지면 provider, policy/supervisor DB 전에 차단한다.

## 검증

- reader + dispatcher focused: **6 passed**
- cycle/supervisor integration: **20 passed**
- runtime actionability related: **40 passed**
- full suite: **2515 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- supervisor one-cycle fixture: READY, historical/current GET 21, actionability manifest 1
- manual cycle QA: exit 0, data GET 1, manifest 1, report `1 new, 0 replay`
- partial-option QA: exit 1, provider open 0, policy DB 0, manifest root 0, account/order mutation 0

## 남은 경계

- runtime supervisor는 manifest를 자동 생성하지만 dynamic WebSocket connection owner와 projection CLI를 아직 dispatch하지 않는다.
- 다음 checkpoint는 manifest plan별 bounded read-only connection, receipt terminal과 projection을 한 운영 lifecycle로 연결한다.
- 실제 provider 연결은 열린 NYSE 정규장, explicit read-only arm, private SIP credential과 bounded frame/timeout이 모두 맞을 때만 허용한다.
- manifest와 derived actionability는 Telegram delivery나 Paper order intent가 아니다.
