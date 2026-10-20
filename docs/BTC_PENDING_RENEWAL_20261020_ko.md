# 미발동 조건부 예약의 명시적 재승인 (M0/M3/M4, version 1)

## 구현 전 6항 검토

1. **기준선/유형:** `22012085`에서 사용자 승인된 대기 정책 변경이다. 아직 Untriggered이며
   체결0인 새 조건부 OPEN만 대상으로 한다. 원래 생성 시각부터 절대1시간, 매5분 명시적
   재승인, 다음 판단 경계 이후 최대120초 처리 여유다. 기존 주문에는 소급 적용하지 않는다.
2. **가설/반증:** 구조가 살아 있지만 트리거에 도달하지 않은 예약을 불필요하게 취소하지
   않는 목적이다. 오래된 신호 노출·오류시 대기 증가 위험이 있다. 재승인 실패/누락/지연,
   기존 lease 만료, 1시간 상한, 체결·발동·UNKNOWN·취소중은 연장 사유가 아니다.
3. **경로/권한:** 모델의 selected WAIT.pending_renewal은 host offer 식별자만 선택한다.
   원래 등록 계약의 현재 INTACT 평가와 동일 scenario/child/link/order/request/가격/수량/
   방향/SL/위험이 필수다. core reserved-risk 검산→fresh review guard→durable intent/revision→
   executor 최신 계좌/child/위험/증거 확인→local lease와 APPLIED receipt 원자 저장 경로다.
   거래소 주문 생성/수정/취소교체를 호출하지 않는다. 일반 WAIT·DRAFT만으로 연장하지 않는다.
4. **시간 모델:** 원본 OPEN.expires_at와 회복 permit.entry_deadline은 신규 제출 권한으로
   보존한다. 새 version1 조건 주문의 실제 대기 마감은 별도 host lease이며 최초는
   next input 5m boundary+120, 이후는 해당 재승인 slot의 next boundary+120으로 계산하되
   original created_at+3600을 넘지 않는다. 만료 전에만 갱신하고 같은 slot 중복은 금지한다.
   원본 OPEN/테이프/계약은 불변이며 broker latest evidence만 실제 관측에 따라 갱신한다.
5. **사전 검증:** 정상·롱숏·동일slot/중복/restart·commit-before-apply crash·APPLIED 이후
   재호출·매5분 반복/1시간/120초/응답만료 경계·단계0/1 soft halt와 hard guard 구분·
   부분체결/New/Triggered/UNKNOWN/취소중·가격/수량/SL/identity/위험 변조·legacy 불변을
   fake broker와 임시DB로 검증한다. plain WAIT의 기존 동작, exact frozen review replay,
   전체 오프라인 suite/정확 head CI/100KB 입력 예산도 확인한다. 실제 시험 주문은 금지한다.
6. **현재 결론/중단:** 사용자 승인 데모 구현이며 수익·안전 완성 입증이 아니다.
   구현/테스트/CI/배포/자연 재승인·보호·체결을 구분한다. 오류시 기존 lease를 늘리지 않고
   보호·취소 경로를 유지한다. 새 상주 호출·cron·모델·위험 예산은 추가하지 않는다.
   version1 활성 예약이 있으면 이를 모르는 코드로 즉시 되돌리지 않고 보호/정산부터 한다.

## 내구성 경계

불변 anchor는 최초 OPEN commit에만 생성한다. mutable lease와 APPLIED/REJECTED 기록은
별도 local table에 저장해 runtime state 저장이 이미 적용된 lease를 덮어쓰지 못하게 한다.
APPLIED는 정확한 intent에만 유효하며 lease 갱신과 같은 트랜잭션이다. APPLIED 없는
childless WAIT는 성공으로 추정하지 않는다. 실행 전 crash는 새 거래소 작업 없이 명시적으로
REJECTED 처리할 수 있고, APPLIED 후 crash는 동일 기록으로 멱등 복구한다.

대기 권한은 이미 존재하는 원래 child에만 적용한다. 회복 허가의 원래 OPEN 재제출·증액·
반대 방향·다른 child 생성 권한을 늘리지 않는다. 단계0/1의 soft three-loss 표시는 유지하고
실제 hard guard와 유효한 기존 permit을 별도로 검사한다. 원본 pending expiry를 전역 무시하지
않고, 유효 lease가 있는 child의 effective expiry만 broker 증거로 갱신해 엄격 비교를 유지한다.

## 선택과 알림

갱신을 제안하면 기존 SELECT 단계를 생략하지 않는다. 총 호출은 DRAFT/SELECT 최대2회이며
추가 검증 모델은 없다. 원문 DRAFT의 WAIT는 WAIT_RENEWAL 선택지로 보존하고, host-origin과
원본 hash가 명시된 별도의 갱신 없는 WAIT 선택지를 제공한다. WAIT_RENEWAL 선택만 승인이다.
단일 호출 모드의 갱신 요청은 거절한다. 기존 갱신 없는 WAIT의 호출/스키마는 유지한다.

최초 PLAN은 제출 권한 마감과 실제 host 예약 대기 마감을 구분한다. 갱신 알림은 APPLIED와
같은 트랜잭션의 기존 private outbox를 쓰며 신규 주문/체결로 표현하지 않는다. 알림 오류는
lease 적용을 되돌리지 않는다. 절대 상한 뒤 취소는 기존 보호 루프와 비동기 ACK를 따르므로
취소 확인 전 늦은 체결은 가능하며 실제 체결은 기존 보호 경로로 처리한다.
