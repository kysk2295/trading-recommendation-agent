# Data capability registry 체크포인트

## 완성 범위

- immutable source entitlement와 시점별 capability health assessment를 분리한다.
- entitlement ID는 exact payload만 idempotent하게 허용하고 동일 source의 유효기간 중첩을 차단한다.
- capability는 source와 UTC assessed time별로 append하고 as-of 최신 상태를 재생한다.
- 요청 source가 없으면 fallback을 만들지 않고 missing source ID로 반환한다.
- manifest 등록과 registry snapshot data gate 재평가를 한 local CLI로 제공한다.

## 무결성

- mode 600, current-user regular file, no-symlink
- `BEGIN IMMEDIATE` writer와 update/delete 차단 trigger
- assessment ID, source/time column, entitlement interval, payload SHA와 canonical JSON 재계산
- malformed schema, row 변조, 겹치는 entitlement와 동일 시각 capability 충돌은 fail-closed

## 의미 수정

broad-scanner entitlement의 `effective_from`은 최신 source 관측시각이 아니다. 2026-07-17 등록 계약 버전의 고정 발효일을 사용하고, 매 cycle 변하는 값은 capability `assessed_at`과 `latest_event_received_at`에만 기록한다.

## 안전 경계

- provider와 credential 접근 0건
- account/order endpoint와 broker mutation 0건
- registry `ready`는 선언 계약과 입력 health의 충족이며 provider 전체 coverage나 수익성을 의미하지 않음

## 다음 단계

- Alpaca SIP runtime audit의 minute-bar health projection
- KR terminal source run의 공시·뉴스·랭킹 health projection
- provider별 correction/deletion cursor와 retention 이행 상태 연결
