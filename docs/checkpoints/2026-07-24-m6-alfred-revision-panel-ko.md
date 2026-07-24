# M6 ALFRED multi-vintage revision panel 체크포인트

상태: **query-only point-in-time panel 구현·actual DFF 두 vintage 검증 완료**

기능 커밋:
`42a42f8671407425e9e1249ac1aebbc60f937cb3`

## 제품 경계

검증된 ALFRED snapshot 2~100개를 외부 재조회 없이 읽어 하나의
content-addressed revision panel로 만든다.

- 입력 파일은 mode `0600` private immutable file이어야 한다.
- 파일명은 snapshot의 semantic ID와 정확히 일치해야 하고 본문도 canonical
  JSON과 byte-exact로 같아야 한다.
- 모든 입력은 `alfred` mode, 동일 series·units·관측 범위여야 한다.
- vintage date와 snapshot ID는 중복될 수 없다.
- 관측일이 해당 vintage보다 미래면 panel 전체를 차단한다.
- vintage 순서는 입력 순서와 무관하게 날짜 오름차순으로 고정한다.
- 각 관측일과 vintage의 cell은 `not_observed`, `missing`, `available`을
  구분한다. 최신값으로 과거 `not_observed`나 `missing`을 채우지 않는다.
- revision은 직전 available vintage 값과 현재 available 값의 exact Decimal
  차이이며 panel validator가 다시 계산해 대사한다.

panel은 source snapshot ID, vintage date, 관측 범위와 모든 cell을 포함한 canonical
bytes의 SHA-256을 semantic ID로 사용한다. 동일 입력의 순서를 바꿔 재실행해도 같은
artifact를 재생한다. 이 경로에는 credential, provider, hypothesis, strategy,
trial, Reviewer, lifecycle, broker, account, order 또는 allocation mutation이 없다.

## actual ALFRED DFF 증거

기존 2026-07-23 vintage DFF snapshot과 같은 관측 범위의 2026-07-22 vintage를
공식 ALFRED GET으로 새로 수집했다.

- observation range: `2026-07-01..2026-07-22`
- 2026-07-22 vintage:
  - first official GET/network: `1/1`
  - observations: `21`, missing `0`
  - capability: `complete`, completeness `10000 bps`
  - snapshot ID:
    `83790eb45ba7e6293d4d78f65ec3eb7d502e64792fbd09297a16eb2b6a7911ea`
  - snapshot file SHA-256:
    `0963c7414f6a7272b456d6b3d3ec49591f5944fcfde5c5adb151badf3fd87124`
  - missing credential exact replay: network `0`, artifact created `no`
- 2026-07-23 vintage:
  - observations: `22`, missing `0`
  - snapshot ID:
    `08fb874aa01d8da343b6fce25299ee8df37bbc253a9bfc23802b89ce7149a123`
  - snapshot file SHA-256:
    `3c29084c9b0c2c8b531abcfeb9d1482df8074ad38dab87db7df7ae8713db773c`

exact committed runtime의 panel 결과:

- vintage count: `2`
- observation rows: `22`
- comparable revisions: `21`
- changed revisions: `0`
- `not_observed` release 차이: 보존
- panel ID:
  `f531cb32bd53b2b7e413055ae8b5b37e44b778657aa8ec768ed08c7585ad830a`
- panel file SHA-256:
  `a82aa22197a436af13df6790bc552bedda3b772f5d7e06cefc5858850eb5bea6`
- first/replay artifact created: `yes/no`
- replay provider/credential access: `0/0`
- evidence files: `11`, mode `0600` 아닌 파일 `0`
- credential 값이 evidence에 존재한 파일: `0`

`changed revisions=0`은 이 두 DFF vintage의 비교 결과이며 revision logic이나 다른
series의 무수정을 일반화하지 않는다. 두 번째 vintage에서 관측 하나가 새로 등장한
release availability 차이는 revision 값으로 오분류하지 않고 `not_observed →
available`로 보존했다.

## TDD·검증

- RED: revision panel module 부재로 collection error
- focused ALFRED panel·FRED/ALFRED regression: `6 passed`
- full pytest: `3649 passed in 234.96s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI isolated `--help`: exit `0`
- single-snapshot bad input: exit `2`, output 생성 `0`
- fixture happy/replay: exit `0/0`, artifact `yes/no`
- actual happy/replay: exit `0/0`, artifact `yes/no`

이 panel은 point-in-time 연구 입력 증거다. release calendar의 공식 발표시각,
다중 series macro regime, 전략 성과, champion 또는 주문 권한은 아직 만들지
않는다. 다음 운영 우선순위는 예약된 실제 forward의 clean strict closeout과 causal
dataset·READY v2·독립 Reviewer 결과 검증이다.
