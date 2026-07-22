# US Systematic Regime Shadow Vertical 체크포인트

## 결과

- `us_equities/systematic_quant/regime_rotation`의 첫 실제 systematic strategy vertical을 구현했다.
- 입력 universe는 `GLD`, `IEF`, `IWM`, `QQQ`, `SHY`, `SPY`로 고정했다. fixture와 production 모두 201개 이상의 정렬된 완료 일봉과 동일 세션 coverage를 요구한다.
- SPY 종가가 200세션 평균 위이고 20세션 momentum이 양수이며 SPY/QQQ/IWM 중 2개 이상이 50세션 평균 위이면 `risk_on`이다. 세 조건의 반대쪽과 breadth 1 이하가 함께 맞으면 `risk_off`, 나머지는 `mixed`다.
- risk-on sleeve는 IWM/QQQ/SPY, risk-off sleeve는 GLD/IEF/SHY다. 각 sleeve의 60세션 momentum 상위 2종만 다음 정규장 조건부 후보가 된다. `mixed`는 후보를 억지로 만들지 않고 `no_recommendation` 카드로 보존한다.
- historical replay는 결정 세션까지의 봉으로만 regime과 후보를 정하고 다음 세션 open→close 동일가중 수익에서 고정 왕복 40bp를 차감한다. 마지막 완료 일봉은 결과 계산에 쓰지 않고 당일 read-only 카드에만 사용한다.

## 운영 수직

`run_us_systematic_regime.py`는 현재 뉴욕 거래일만 허용한다.

- 장전: source 없이 no-op
- 정규장: 사전등록된 대상 세션 trial을 실제 호출 시각으로 `started`
- 장후: 대상 세션 outcome과 terminal event를 확정하고, 같은 완료 source로 다음 세션 카드·trial을 등록

카드·outcome은 전용 mode-600 append-only SQLite에 저장하고, hypothesis·code-coupled strategy version·`shadow_forward` trial·`experimental_shadow` lifecycle은 기존 global experiment ledger에 저장한다. exact replay는 새 행을 만들지 않는다.

## 권한 경계

- `market_regime`은 market context와 signal evidence만 생산한다.
- 카드에는 `order_authority=false`, `account_authority=false`, `allocation_authority=false`가 고정되어 있다.
- production source도 Alpaca market-data 일봉 GET만 사용한다. Paper API, 계좌, 포지션, 주문, POST, DELETE, 외부 메시지 경로는 없다.
- 이 vertical은 Allocation Manager, champion 승격, 일반 목적 scheduler를 만들지 않는다.
- fixture QA에서는 자격증명 파일을 읽거나 실제 provider를 호출하지 않았다. 실제 production GET 증거는 아직 0건이다.

## 실행

```bash
uv run python run_us_systematic_regime.py \
  --session-date 2026-07-22 \
  --fixture-root examples/us_systematic_regime/2026-07-22 \
  --database /private/tmp/us-systematic-regime.sqlite3 \
  --experiment-ledger /private/tmp/us-systematic-experiment.sqlite3 \
  --output-dir /private/tmp/us-systematic-regime-output
```

production에서는 `--fixture-root`를 생략한다. current NYSE post-close가 아니면 credential loader와 HTTP client 전에 차단한다. 운영 checkout은 clean이어야 하며 현재 commit SHA가 strategy version에 결합된다. private SQLite와 lock은 symlink·hard-link를 거부하므로 macOS 임시 경로도 `/tmp` alias가 아닌 canonical `/private/tmp` parent를 사용한다.

커밋된 `2026-07-22` fixture는 고정 universe의 ETF별 201개 정렬 세션을 가진 합성
`risk_on` 입력이다. 현재 날짜 CLI 경계, signal-only 카드와 private ledger를 재현하기
위한 QA 자료이며 실제 시장 데이터, 현재 추천, 전략 성과 또는 승격 근거가 아니다.

## 검증

최종 commit의 정확한 수치는 branch 완료 보고에 기록한다. 필수 gate는 다음과 같다.

- systematic engine/source/store/trial/operating focused tests
- 전체 `pytest`
- 변경 Python 파일 전체 Ruff와 basedpyright
- CLI `--help`, malformed date, fixture post-close happy path
- 결과 카드·보고서 mode 600과 account/order/POST count 0 확인

## 남은 실제 증거

- 다음 자연스러운 current NYSE post-close에서 bounded read-only 일봉 GET을 별도 운영 체크포인트로 확인한다.
- 이후 각 대상 정규장에 실제 시각의 `started`와 장후 outcome을 누적한다.
- 충분한 독립 표본, 동일 위험 비교, Reviewer 근거 전에는 shadow 상태를 유지한다.
