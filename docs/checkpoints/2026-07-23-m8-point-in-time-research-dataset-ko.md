# M8 실제 point-in-time 연구 데이터셋 체크포인트

## 제품 결과

보존된 US forward-session 디렉터리를 실제 historical research 입력으로 바꾸는
`run_intraday_research_dataset.py`를 추가했다.

```text
strict forward-session quality gate
-> complete symbol coverage
-> candidate first-observation causality cut
-> as-of prior close / ADV / spread join
-> content-addressed bounded CSV + source receipt
-> existing M8 multi-strategy walk-forward + Reviewer
```

새 materializer는 provider, credential, account, broker를 import하지 않는다. 결과 CSV는
기존 `run_intraday_research_loop.py --input-csv`가 그대로 읽을 수 있다.

## 인과성·불변성

- 요청한 세션 하나라도 기존 `load_replay_source` 품질 게이트를 통과하지 못하면 전체를
  차단하고 부분 CSV를 발행하지 않는다.
- 중복 거래일과 동일 exchange/symbol/timestamp bar를 차단한다.
- complete coverage 종목만 대상으로 삼는다.
- candidate context가 관측되기 전에 시작한 bar는 연구 입력에서 제외한다.
- 각 bar에는 bar 시작시각 이전 가장 최신 point-in-time context의 `prior_close`,
  `average_daily_volume`, `spread_bps`를 결합한다.
- 최대 60세션, 100,000 bar이며 기존 연구 루프의 64 MiB·9.5 GiB 게이트는 유지된다.
- CSV SHA-256을 파일명과 v2 manifest `input_sha256`으로 사용한다.
- receipt는 session date, exact source-session content SHA-256, eligible/censored
  symbol-session 수와 bar 수를 기록한다.
- CSV와 receipt는 content-addressed immutable mode `600` 파일이며 exact replay는
  파일을 교체하지 않는다.

## 실제 저장 세션 결과

기존 `outputs/live_sessions/{20260715,20260716,20260721,20260722}` 네 세션을 새 CLI에
동시에 전달했다. 기존 품질 결손이 그대로 검출되어 exit `1`, report `blocked`,
발행 CSV `0`건이었다. 결손을 무시하거나 성공 cycle만 사후 선택하지 않았다.

따라서 이번 체크포인트의 실제 historical trial, 성과, 승격, champion claim은 모두
`0`이다.

## 수동 CLI QA

- `--help`: exit `0`, session·세션 수·bar 예산 옵션 노출
- 존재하지 않는 session: exit `1`, blocked
- 기존 실제 4세션: exit `1`, blocked, CSV `0`
- 완전한 1세션 fixture: exit `0`, candidate 관측 전 6분 제외, causal bar `384`
- fixture exact replay: 기존 CSV·receipt 재사용
- CSV·receipt 권한: `600`
- provider/account/order mutation: `0`

## 검증

- 새 materializer·기존 replay focused: `8 passed`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: `3389 passed`, 기존 `development_harness/Grok` 오프라인 환경 테스트
  `5 failed`

다음 운영 단계는 수집 품질을 완화하는 것이 아니라 완전한 세션을 새 materializer에
누적하는 것이다. 생성된 exact CSV SHA-256과 실제 READY data foundation을 source-backed
v2 manifest에 사전등록한 뒤 기존 walk-forward와 독립 Reviewer를 실행한다.
