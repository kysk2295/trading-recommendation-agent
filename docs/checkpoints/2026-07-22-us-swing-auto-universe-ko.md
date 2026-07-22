# US Swing Most-Active 자동 Universe 체크포인트

## 결과

- Alpaca market-data의 `GET /v1beta1/screener/stocks/most-actives`를 strict read-only client로 연결했다.
- `by=volume`, `top=50`으로만 요청하며 응답의 `last_updated`, `symbol`, `volume`, `trade_count`를 frozen model로 파싱한다.
- symbol 중복, volume 순위 역전, 50개 초과, 미래 관측, 다른 뉴욕 거래일 snapshot은 fail-closed 한다.
- 원본 rank 순서는 검증에 보존하고 completed-daily scanner에는 정렬된 symbol 집합만 전달한다.
- `run_us_swing_shadow.py --auto-universe`와 `run_us_swing_operating_session.py --auto-universe`가 같은 경로를 사용한다.
- 계좌, 잔고, 포지션, 주문 endpoint와 HTTP POST는 추가하지 않았다.

공식 계약: <https://docs.alpaca.markets/us/reference/mostactives-1>

## 실행

미국 정규장 종료 뒤 현재 뉴욕 거래일로 실행한다.

```bash
uv run python run_us_swing_operating_session.py \
  --session-date YYYY-MM-DD \
  --auto-universe
```

수동 파일을 사용하는 기존 `--universe-file`과 fixture `--fixture-root`는 유지되며 세 옵션은 상호 배타적이다.

## 실제 read-only 확인

- 2026-07-22 미국 장전 capability GET: HTTP 200
- 응답 필드: top-level `last_updated`, `most_actives`; row `symbol`, `trade_count`, `volume`
- 응답 snapshot 뉴욕 날짜: 2026-07-21
- 현재 뉴욕 날짜: 2026-07-22
- 결과: 전일 snapshot이므로 오늘 universe 사용을 거부
- 외부 mutation: 0
- 자격증명·계좌 식별자 출력: 0

## 검증과 남은 증거

- focused client/CLI tests: 13 passed
- Swing 회귀: 68 passed
- 전체 테스트: 3284 passed
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings
- no-excuse checker: 변경 production 4개 파일 위반 0
- 오늘 미국 정규장 종료 뒤 current-session snapshot으로 auto universe → completed daily bars → Hermes cycle → trial registration을 실제 원장에서 완주해야 한다.
- 이 연결은 후보 수집 기능이며 전략 성과, champion 승격 또는 Allocation 입력 증거가 아니다.
