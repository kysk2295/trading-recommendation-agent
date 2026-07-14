# KIS 읽기 전용 서버 오류 재시도 체크포인트

작성 시각: 2026-07-15 KST

## 운영 문제

실제 2026-07-14 정규장 watcher는 랭킹 6개 요청이 모두 성공한 cycle에도 일부 선택·추적 종목의 분봉 또는 일봉 HTTP 500 때문에 종료코드 1을 유지했다. 성공 종목은 보존됐지만, 매 cycle의 일부 입력이 빠져 적격 forward day로 셀 수 없는 상태였다.

## 수정 계약

- 대상은 KIS 랭킹·분봉·일봉·현재가상세 읽기 전용 GET뿐이다.
- HTTP 500/502/503/504만 80ms 뒤 정확히 한 번 재시도한다.
- 두 번째 서버 오류는 그대로 호출자에게 전달한다.
- 429는 즉시 전달하며 숨은 추가 호출로 rate limit을 악화시키지 않는다.
- 첫 실패 뒤 두 번째 응답이 실제 성공한 경우에만 정상 입력으로 사용한다.
- 반복 실패 observation과 cycle 비영 종료코드는 유지한다.
- 주문 제출·취소·교체 등 mutation 경로에는 적용하지 않는다.
- 매 scan cycle의 재시도·복구·최종 실패 수를 `kis_read_retry_cycles.csv`에 남긴다.
- 재시도 event는 인증정보 없이 endpoint path·거래소·종목·HTTP status만 별도 CSV에 남긴다.
- watch cycle과 retry audit cycle 수가 다르면 해당 날짜는 적격 forward day로 세지 않는다.

## 검증

- mock 500→200은 요청 2회 뒤 성공 응답을 반환했다.
- mock 500→500은 요청 2회 뒤 500을 유지했다.
- mock 429는 요청 1회만 수행했다.
- mock 500→429는 성공 복구가 아니라 최종 실패로 분류했다.
- opening-gap의 첫 500→200 성공은 같은 cycle의 정상 snapshot으로 저장되고 다음 cycle에서 캐시 재사용됐다.
- AMEX 랭킹 500→500은 실패 1건과 정상 그룹 5개를 유지했다.
- 수동 QA에서 재시도 2건을 복구 1건·최종 실패 1건으로 분리한 cycle/event CSV를 확인했다.
- 일일 연구 CLI는 두 감사 CSV를 checksum 계보에 포함하고 복구 건수를 운영 incident로 기록했다.
- 전체 회귀 436개, Ruff 변경 파일 검사, 포맷 검사와 basedpyright가 통과했다.

## 실제 watcher 적용 결과

- 적용 직전 01:00 KST cycle은 종목별 HTTP 500 observation 8건과 종료코드 1이었다.
- 적용 후 01:01 cycle은 observation 오류 0건, 종료코드 0으로 완주했다.
- 다음 01:02 cycle은 AXTU 분봉이 첫 요청과 단 한 번의 재시도에서 모두 500이어서 observation 오류 1건, 종료코드 1을 유지했다.
- 따라서 bounded retry가 일시 오류 일부를 복구한다는 것은 확인됐지만 공급자 반복 오류를 제거하지는 않는다.
- 이 날짜는 앞선 실패 cycle과 장중 도입 전 coverage 누락 때문에 적격 forward day로 세지 않는다.

이 변경은 데이터 공급자 오류를 성과 0이나 성공으로 바꾸지 않는다. 실제 watcher 다음 cycle에서 반복 실패 수가 줄어드는지 별도로 관찰하며, 한 날짜의 수익성 또는 전략 우위 증거로 사용하지 않는다.
