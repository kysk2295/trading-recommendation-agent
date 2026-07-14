# Alpaca Paper 보호 OCO 원장·nested 복구 체크포인트

## 결론

부분체결 보호 OCO를 계산만 하던 단계에서, 사전 계획과 broker의 nested take-profit/stop leg를 서로 분리해 불변 원장에 보존하고 현재 heartbeat 구간 안에서 정확히 대사하는 단계까지 진행했다. 다만 실제 Paper 계좌에는 열린 OCO가 없어 실계좌 두 leg 관측은 0건이다. 따라서 이번 결과는 **주문 실행 안전 기반 검증**이며 실제 보호 주문 제출이나 전략 수익성 증거가 아니다.

## 구현된 경계

- 실행 원장을 schema v5로 올리고 `protective_oco_plans`와 `paper_recovery_protective_oco_legs`를 추가했다.
- 계획은 entry intent, 결정론적 보호 client order ID, symbol, side, 수량, 2R limit, stop을 pre-submit 불변값으로 저장한다.
- 같은 identity의 수량 증가는 새 계획 행으로 남기고 가격·symbol·side를 덮어쓰는 충돌은 거부한다.
- Alpaca `GET /v2/orders`의 open/recent 이력을 `nested=true`로 읽고 entry 주문과 OCO를 별도 inventory로 분리한다.
- OCO는 limit take-profit 부모 하나와 stop-market 자식 하나만 허용한다. bracket, OTO, mleg 또는 불완전 leg는 전용 경계에서 거부한다.
- recovery마다 두 leg를 정규화해 저장하고 OCO hash를 부모 recovery identity에 결합한다.
- 현재 admission은 계획, 포지션, 남은 두 leg 수량, 가격, side, DAY TIF, extended-hours 금지와 REST 수신시각을 모두 확인한다.
- 과거 heartbeat 구간의 OCO, 미계획 OCO, 잘못된 stop, 중복 또는 미확인 OCO는 신규 진입을 fail-closed한다.

## migration 결함과 수정

실제 기존 원장 복사본에서 첫 v5 GET-only 복구는 종료코드 2로 실패했다. 원인은 v3 recovery key가 Account Activities hash 도입 전 공식으로 생성됐지만, migration은 빈 hash 컬럼만 추가하고 immutable·foreign-keyed key를 바꾸지 않았던 점이다.

실제 두 기존 행은 snapshot과 orders hash가 정상이고 저장 key가 v3 공식과 정확히 일치했다. 수정 후에는 다음 두 조건을 모두 만족하는 행에만 v3 key를 허용한다.

- `activities_sha256`가 정확한 빈 집합 hash
- `protective_ocos_sha256`가 정확한 빈 집합 hash

새 recovery는 계속 activity hash를 포함하고, OCO가 있으면 OCO hash도 포함하는 v5 key만 생성한다. 보호 hash 변조 회귀는 부모 recovery 조회 단계에서 거부된다.

## 실제 Paper GET-only QA

기존 실행 원장의 복사본을 v5로 migration한 뒤 실제 명령을 tmux에서 실행했다.

```bash
./run_alpaca_paper_recovery.py \
  --database /tmp/paper-oco-recovery-actual.sqlite3 \
  --output-dir /tmp/paper-oco-recovery-actual-report
```

관측 결과:

- 종료코드: 0
- recovery 행: 2 → 3
- 정규화 entry 주문: 0건
- Account Activities FILL: 0건
- 보호 OCO: 0건
- execution 상세: 완전
- 차단 사유: 없음
- 원장 재조회와 reconciliation ledger 재구성: 성공
- 외부 동작: Alpaca Paper WSS + REST GET only
- POST/PATCH/DELETE: 비활성

OCO가 0건이므로 실제 broker가 반환한 nested leg의 가격·수량을 대사했다는 주장은 하지 않는다. 그 경계는 엄격 파서·MockTransport·원장·런타임 회귀로 검증됐고, 실제 OCO 제출이 안전하게 공개된 뒤 별도 Paper smoke가 필요하다.

## 검증

- 전체 pytest: 500개 통과
- OCO·migration·recovery 표적 회귀: 통과
- 변경 Python: Ruff, Ruff format, basedpyright, no-excuse 검사 통과
- CLI: `--help`, 누락 원장 오류, 실제 Paper GET-only happy path 직접 실행
- 순수 코드 파일: 250줄 이하

## 다음 안전 게이트

1. 단일 Writer가 부분체결 직후 저장된 계획으로 OCO POST를 정확히 한 번 제출하는 경계
2. fill 20→35주 증가 시 기존 OCO의 취소·교체와 모호한 timeout 복구
3. 신규 진입 cutoff와 일손실 kill switch
4. 폐장 전 미체결 취소와 EOD 강제 평탄화
5. 위 경계를 실제 Alpaca Paper에서 검증한 뒤에만 동시 1포지션 ORB pilot 공개

60거래일·100건은 이 구현을 기다리는 기간이 아니다. 안전 게이트가 끝나는 즉시 소규모 Paper pilot을 시작하고 5/10/20/60일 롤링·시장 국면·종목 특성 cohort로 매일 중단 여부를 다시 판단한다.
