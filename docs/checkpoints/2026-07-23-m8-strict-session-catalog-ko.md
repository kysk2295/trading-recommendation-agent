# M8 strict forward-session 누적 catalog 체크포인트

## 닫은 결손

기존 materializer는 요청한 세션 중 하나라도 실패하면 전체 dataset을 차단한다. 이는
단일 실험 입력의 fail-closed 계약으로는 맞지만, 매일 쌓이는 운영 세션에서 최소 20개
clean session을 누적하려면 결과와 무관한 사전 품질 선택 계층이 별도로 필요했다.
날짜별 dataset만 만들면 매 실험 version이 계속 한 세션에 머물러 Reviewer의 성숙도
기준에 도달할 수 없다.

## 구현 계약

`run_intraday_research_dataset_catalog.py`는 각 후보 session directory를 기존
`load_replay_source`로 독립 감사한다.

- ranking/watch/candidate/post-session 품질, 인과성, 정규장 coverage 기준은 기존과 같다.
- 적격성은 전략 성과나 추천 수가 아니라 source 품질만으로 결정한다.
- blocked session을 삭제하거나 0수익으로 바꾸지 않고 reason code와 함께 catalog
  receipt에 남긴다.
- unique session name과 거래일을 요구하고, 최소 clean session 수 미달은 dataset
  publication 전에 차단한다.
- 예약 거래일을 `required_session_date`로 고정하면 그 날짜 자체가 clean selection에
  포함되지 않는 한 과거 clean session만으로 새 dataset을 재발행하지 않는다.
- 적격 세션이 최대 60개를 넘으면 날짜순 최신 60개만 bounded 입력으로 선택한다.
- 선택된 집합은 기존 strict materializer를 다시 통과하므로 catalog 판정과 dataset
  판정이 다르면 publication이 차단된다.
- exact dataset SHA, canonical dataset receipt 이름, 선택 source SHA, 전체
  eligible/blocked audit를 content-addressed mode-600 catalog receipt에 고정한다.
- provider, credential, account, broker 또는 주문 endpoint를 import하거나 호출하지
  않는다.

## 검증

- 두 clean session과 한 blocked session: 선택 `2`, blocked `1`, causal bar `768`
- minimum clean floor 미달: CSV/dataset receipt/catalog receipt `0`
- exact replay: dataset과 catalog receipt 재사용, 신규 artifact `0`
- CLI help, minimum floor bad path, happy path, exact replay: `0/1/0/0`
- happy/replay artifact mode: 모두 `600`
- 기존 실제 4개 session audit: exit `1`, report `blocked`, data artifact `0`
- dataset/binding/source-backed loop 회귀 포함: `29 passed`
- 전체 pytest: `3410 passed`
- changed-file Ruff, basedpyright `0 errors, 0 warnings`, compileall 통과
- external mutation: `0`

다음 운영 단계는 7월 23일과 24일 strict session을 이 catalog에 함께 전달해 clean
세션만 누적한 exact dataset을 만들고, READY foundation/v2 manifest와 독립 Reviewer로
연결하는 것이다. 한 세션이 품질 차단되어도 다른 clean 세션을 사후 성과 선택 없이
보존할 수 있지만, 최소 20 clean session·30 completed trade 전 promotion 기준은
완화하지 않는다.
