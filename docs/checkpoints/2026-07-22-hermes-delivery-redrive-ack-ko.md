# Hermes Delivery Redrive와 실제 ACK 체크포인트

기준 직전 커밋: `b86ef55b36a08006897e440cfc84f189bf44a936`

## 판정

- launchd single-worker가 실제 Telegram 전송 acknowledgement를 append-only delivery 원장에 기록했다.
- timeout dead letter는 명시적 CLI redrive로만 새 root event가 되며 같은 transition 재실행은 replay로 끝난다.
- 같은 ACK event는 redrive CLI 재실행과 delivery-service 재시작 뒤에도 다시 claim되지 않았다.
- 이 범위의 Milestone 1 Hermes 실제 전달 증거는 완료다.

## 원인과 수정

기존 sender는 global Hermes env를 먼저 읽고 stockagent profile env를 나중에 읽었다. 그 결과 profile bot
token과 global home channel이 한 process에서 결합될 수 있었다. Telegram read-only `getChat`은 이 조합을
거부했다.

sender는 이제 활성 `HERMES_HOME` profile만 읽는다. profile에 home channel이 있으면 그것을 사용하고,
없으면 `TELEGRAM_ALLOWED_USERS`가 정확히 한 개의 숫자형 사용자 ID일 때만 그 owner DM을 대상으로 쓴다.
허용 사용자가 없거나 여러 명이거나 숫자형이 아니면 network send 전에 fail-closed한다. credential,
사용자 ID, chat ID와 message ID는 CLI 출력이나 이 문서에 기록하지 않았다.

## Redrive 계약

`run_hermes_delivery.py redrive`는 다음 조건을 모두 만족하는 transition만 받는다.

- 실제 append-only 원장에 정확히 한 건 존재하는 dead letter
- reason이 `telegram_timeout`
- 원본 event에 acknowledgement가 없음
- 마지막 attempt와 dead-letter transition이 일치하고 최대 시도 횟수가 소진됨
- 원본이 독립 root delivery event

새 source identity와 payload digest는 원본 delivery와 dead-letter transition으로 결정된다. 새 event는 원본
내용과 provenance를 복사하고 dead-letter transition evidence를 추가한다. 같은 transition을 다시 redrive하면
새 event를 만들지 않고 `inserted=0`, `replayed=1`을 반환한다. terminal rejection과 알 수 없는 transition은
차단한다.

## 실제 운영 증거

- launchd label: `ai.trading-agent.hermes-delivery`
- sender profile: stockagent 전용 `HERMES_HOME`
- credential/identifier 비출력 read-only Telegram `getChat`: 성공
- redrive 전 원장: events 2, attempts 6, acknowledgements 0, dead letters 2
- redrive 후 원장: events 3, attempts 7, acknowledgements 1, dead letters 2
- 같은 transition 재실행: `inserted=0`, `replayed=1`, 원장 수치 불변
- delivery-service만 강제 재시작: running, 원장 수치 불변
- 설치된 stockagent plugin sender와 repository source: 동일

stockagent gateway는 중단하거나 재시작하지 않았다. 실제 금융 주문, broker mutation, Alpaca live endpoint,
KIS 또는 LS 주문 endpoint는 사용하지 않았다.

## 검증

- Hermes 집중 회귀: 59 passed
- 전체 pytest: **3254 passed in 188.24s**
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings, 0 notes
- compileall과 Hermes Python 3.11 py_compile: 통과
- no-excuse 검사: 5개 변경 Python 파일 모두 통과
- CLI `--help`: redrive command 노출 확인
- CLI `redrive --help`: required transition option 확인
- 알 수 없는 transition: exit `2`, database 미생성, redacted blocked result
- 실제 timeout redrive: Telegram acknowledgement 1건
- 실제 replay와 서비스 재시작: 추가 attempt 및 acknowledgement 없음
