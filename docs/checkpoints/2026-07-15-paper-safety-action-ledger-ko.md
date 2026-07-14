# Alpaca Paper cutoff·kill switch·EOD 안전조치 원장 체크포인트

## 결론

고정 60거래일을 기다리지 않고 작은 Paper pilot을 시작하기 위한 다음 선행조건으로, 신규 진입 cutoff·일손실 kill switch·EOD 평탄화의 **현재시점 조치 계획과 재시작 latch**를 구현했다. 이번 단계는 broker 주문을 변경하지 않는다. 실제 OCO 제출·취소·포지션 청산은 여전히 닫혀 있으므로 전략 수익성이나 자동매매 완료 증거가 아니다.

## 안전 계약

- 계좌·시장 clock·포트폴리오·보호 OCO 수신시각이 평가시각 기준 5초 안에 있고 Alpaca와 로컬 정규장 경계가 같아야 한다.
- 15:30 ET부터 남은 entry를 취소 대상으로 계획한다.
- MTM 일손익 또는 열린 포지션 계획위험까지 차감한 보수적 일손익이 -USD 300 이하이면 시간대보다 kill switch가 우선한다.
- 15:55 ET부터 entry, 보호 OCO 부모, exact broker position을 순서대로 취소·평탄화 계획에 넣는다.
- 동일 cancel order ID, 중복 position symbol, 0·소수·비정상 수량은 거부한다.
- 당일 kill plan은 equity가 회복되거나 프로세스가 재시작돼도 신규 진입을 계속 차단하고 남은 포지션의 kill 계획을 다시 만든다. 다음 뉴욕 거래일에는 만료된다.

## 불변 원장

schema v6의 `paper_safety_plans`와 `paper_safety_actions`는 계좌 fingerprint, 관측시각, 뉴욕 거래일, 단계, 두 손익, 순서화된 조치 hash를 결합한다. 부모와 자식 행 모두 UPDATE·DELETE trigger로 수정이 금지된다. 단일 Writer만 저장하며 exact replay는 새 행을 만들지 않는다. v0~v5 원장은 writer open 때 v6로 순차 migration한다.

## 실제 Alpaca Paper QA

기존 실제 실행 원장 SQLite backup을 사용해 다음 명령을 직접 실행했다.

```bash
./run_alpaca_paper_safety.py \
  --database outputs/paper_execution/safety_actual_20260715/execution_copy.sqlite3 \
  --output-dir outputs/paper_execution/safety_actual_20260715/report
```

관측 결과:

- 종료코드 0
- 기존 schema v3 → v6 migration 성공
- 실제 WSS 인증·구독과 current-epoch REST GET recovery 성공
- 첫 관측시각 2026-07-14 15:48 ET, 단계 `entry cutoff`
- 재관측시각 2026-07-14 15:55 ET, 단계 `EOD flatten`
- MTM·보수적 일손익 각 0 USD
- entry·보호 OCO·포지션 0건이어서 계획 조치 0건
- safety plan 2건(entry cutoff 1, EOD flatten 1) append, `PRAGMA quick_check = ok`
- 외부 주문 POST/PATCH/DELETE 없음

실계좌에 노출이 없었으므로 두 시점 모두 조치가 0건이었고 실제 cancel·flatten 대상 순서의 broker 관측 증거는 아직 없다. 해당 경계는 타입 모델, migration, append-only 원장, current-epoch generation barrier, kill 재진입 차단 회귀로 검증했다.

## 검증과 다음 게이트

- 전체 pytest 519개 통과
- Ruff lint와 변경 파일 format 통과
- basedpyright 0 오류
- CLI `--help`, 누락 DB fail-closed, v5 migration, 실제 Paper WSS·GET happy path 직접 실행
- 순수 Python 파일 250줄 이하

다음 단계는 이 불변 계획을 실제 Paper mutation과 연결하는 것이다. OCO POST, 취소·교체, cancel/filled race, 모호한 timeout REST 복구, EOD exact flatten을 fake provider와 최소수량 실제 Paper에서 검증한 뒤에만 ORB 동시 1포지션 pilot을 연다. 60거래일·100건은 pilot 시작 대기기간이 아니라 최종 champion 검토 기준이며, 운영 중에는 5/10/20/60일·시장 국면·종목 cohort로 매일 조기 중단한다.
