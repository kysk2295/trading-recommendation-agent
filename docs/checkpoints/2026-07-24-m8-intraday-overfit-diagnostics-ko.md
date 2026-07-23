# M8 Intraday DSR/PBO 진단 체크포인트

작성 시각: 2026-07-24 KST

기능 커밋:
`b88f12e2320f44c180d688a2075dbf0b1219f3c2`

## 이번에 닫은 결손

walk-forward schema v2가 OOS session별 gross/net trade return을 보존하지만,
그 trace를 실제로 읽어 다중검정과 전략 선택 과적합을 재계산하는 독립 surface가
없었다. 집계 평균이나 Reviewer 결론만으로는 DSR 또는 PBO를 검증할 수 없었다.

`run_intraday_overfit_diagnostics.py`는 broker·provider·lifecycle 원장을 쓰지 않는
query-only CLI다. exact completed experiment와 독립 review를 기존 equal-risk
검증기로 다시 대사한 뒤에만 content-addressed mode-600 진단 artifact를 만든다.

## DSR 계약

- 각 전략의 net trade return은 거래일 안에서 복리 결합해 동기 session return으로
  만든다.
- 전략별 Sharpe, 선택 전략의 표본 길이·왜도·첨도, 전략 간 Sharpe 분산을
  artifact 안의 trace에서 다시 계산한다.
- trial 수는 reviewed-at 이하 같은 intraday lane에 등록된
  `historical_replay` 전체를 센다. completed 결과만 세지 않으므로 failed/censored
  시도를 누락하지 않는다.
- 등록 수를 독립 trial 수로 단정하지 않고 `conservative lane trial count`로
  명시한다. 상관된 trial의 유효 독립 수를 추정해 문턱을 완화하지 않는다.
- expected maximum Sharpe와 DSR은 Bailey·López de Prado의 expected-maximum/PSR
  식을 사용한다.

원 수식:

- <https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf>

## CSCV-PBO 계약

- 세 전략의 날짜가 완전히 같은 `T × N` session-return matrix만 사용한다.
- 최소 20개 동기 OOS session, 전략별 최소 30 trades, 세 개 후보를 요구한다.
- session을 각 block 최소 5일인 동일 크기 짝수 partition으로 나눈다.
- 4~16 범위에서 가능한 가장 큰 partition 수를 고르고 모든 대칭 반분 조합을
  IS/OOS로 평가한다.
- 각 IS에서 Sharpe가 유일하게 가장 높은 전략을 선택하고 그 전략의 OOS 상대
  rank logit을 보존한다. 선택 동률, 0분산 또는 동일 크기 partition 불가능은
  수치로 꾸미지 않고 `collecting`으로 닫는다.
- PBO는 음수 OOS rank logit의 비율이다.

원 알고리즘:

- <https://www.carmamaths.org/resources/jon/backtest2.pdf>

## 권한 경계

진단 status는 `collecting | diagnostic_ready`뿐이다. `diagnostic_ready`는 두
통계량을 계산할 입력이 충분하다는 뜻이며 전략이 견고하거나 승격 가능하다는
판정이 아니다.

artifact에는 다음 권한이 모두 `false`로 고정된다.

- automatic state change
- order authority change
- allocation change

winner, champion, Paper arm, 주문, 위험예산 변경은 만들지 않는다.

## 관찰된 QA

20개 동기 session·전략 3개·각 30 trades·보수적 lane trial count 7인 고정
fixture:

- status: `diagnostic_ready`
- selected strategy: `gamma-v2`
- CSCV partitions/combinations: `4/6`
- PBO: `0.0`
- DSR: `0.9949874120895517`
- DSR/PBO 값을 바꾼 self-consistent 위조: model validation 차단

repository 1-session 실제 CLI surface:

- `--help`: exit `0`
- missing source: exit `1`, blocked report, artifact `0`
- first/replay: exit `0/0`
- artifact: `1개`, mode `600`
- candidate/lane trial count: `3/3`
- blockers: `4`
- DSR/PBO: `none/none`
- external mutation: `0`

검증:

- 신규 및 인접 타깃: `14 passed`
- 전체 suite: `3591 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`

## 아직 성공 증거가 아닌 것

현재 repository 1-session fixture와 기존 frozen actual-research runtime은
최소 표본을 충족하지 않는다. 이번 변경은 synthetic/fixture 수익성을 주장하지
않고, 실제 clean forward session에서 schema v2 artifact가 누적된 뒤 동일 CLI로
다시 계산해야 한다.

다음 운영 우선순위는 품질 gate를 완화하지 않은 clean actual forward session,
exact causal dataset/READY manifest, 실제 3전략 walk-forward/Reviewer와 이
진단 artifact의 결속이다.
