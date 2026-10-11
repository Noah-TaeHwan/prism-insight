# BTC 연구 근거 내보내기 1단계

M3 관측 보완이다. 새 매매 규칙·LLM·daemon·cron을 만들지 않고 기존 shipper가 독립 함수를 호출한다. 원본 거래 SQLite는 mode=ro/query_only로 읽으며 수정하지 않는다. 오래된 853MB 원본을 다시 읽는 기본 backfill은 없다.

## 기록과 경계

- 최초 호출은 같은 읽기 snapshot의 각 원본 스트림 MAX(rowid)를 자체 상태 DB에 저장하고 coverage 이벤트를 보낸다. 그 이전 기록과 기존 대기 알림은 소급 보장하지 않는다.
- immutable audit/recovery/broker 스트림과 새 알림 rowid만 증분 처리한다. mutable decision은 OUTCOME의 정확한 slot으로만 결합한다. 알림은 새로 관찰한 QUEUED/SENDING ID만 제한적으로 상태를 재확인한다.
- 자체 outbox와 cursor를 한 transaction으로 저장한다. 원본 교체·rowid 후퇴·마지막 행 본문 변경은 자동 초기화하지 않고 중단한다.
- 모든 쓰기 전에 원본·상태·spool과 SQLite sidecar 경로의 동일 경로·심볼릭 링크·하드 링크 충돌을 검사한다. broker evidence의 정수 PK도 명시적 source_rowid 별칭으로 읽는다.
- spool은 안정된 event ID로 한 줄 전체 쓰기와 fsync를 확인한 뒤 자체 outbox에서 제거한다. 그 사이 장애는 동일 ID 중복이 가능하므로 수집기는 event ID로 중복 제거해야 한다. Exactly-once 보장은 아니다.
- 최초 queue 시 완성된 sanitized 이벤트를 저장하므로 재시도 본문·시각·trace/span도 동일하다. trace는 확인된 scenario 참조, 없으면 정확한 slot/input 참조, 그것도 없으면 개별 원본 키를 사용한다. 동일 DB라는 이유만으로 모든 사건을 한 trace로 묶지 않는다.
- 매번 선행 개행을 포함한 단일 append를 사용해 이전 short-write의 불완전한 줄과 새 JSON이 붙지 않도록 한다. 쓰기 길이·fsync·읽기 대조까지 확인하기 전 outbox를 지우지 않는다. 손상된 이전 접두부는 유지하며 shared spool을 truncate하지 않는다. 기존 shipper는 빈 줄과 손상 줄을 건너뛴다. 다른 작성자와 동시 append로 대조가 불확실하면 outbox를 남겨 같은 ID로 재시도한다.
- 한 호출 최대 100행, 본문 처리 4MiB, 자체 outbox 1,000건 제한이다. 이벤트는 64KiB 미만이다. busy/오류는 작은 상태값으로 반환하며 매매를 막지 않는다.
- 원본 timestamp와 내보내기 관측 시각을 구분한다. 코드 정책은 원본 audit manifest에서만 가져온다. 누락되면 UNKNOWN이며 exporter checkout을 당시 정책으로 사용하지 않는다.
- 모델 요청·응답·체결 원본은 SHA256 참조만 보낸다. 자유문장 rationale, API body, 계좌 식별자, 자격증명은 보내지 않는다. 숫자·상태 코드는 명시적 허용 목록을 사용하고 기존 개인정보 필터를 유지한다.
- 모델명·effort·fast, 요청/응답/프롬프트/schema 해시와 UTF-8 입력 크기를 보존한다. 같은 slot의 요청·응답 journal 시각 차이는 관측 지연이며 provider latency로 부르지 않는다. 알 수 없는 reason/free text는 원문 대신 SHA256과 생략 표시만 보낸다.
- position 단수·복수, 평단/수량/SL, TP/부분 SL 가격·비중, MA 이름표, 이전/제안 SL·위험을 허용 목록으로 보존한다. 유한한 숫자 문자열은 원래 문자열 그대로 보존한다. proposal은 설치된 보호가 아니라 제안, context는 해당 판단 입력 관측임을 구분한다.
- 연결 참조는 원본 문자열 UTF-8 SHA256이며 scenario_id_ref/intent_id_ref/input_id_ref/action_id_ref를 공통 사용한다. 행과 본문 또는 정확한 slot의 context/proposal이 충돌하면 공통 참조를 추정하지 않고 CONFLICT로 남긴다. 원본 body_sha256은 저장 문자열의 정확한 바이트 표현에 대한 해시다.
- 알림 SENT는 원본 상태 관측이다. 실제 발송 시각과 독자 수신은 UNKNOWN이다. API ACK를 체결로 바꾸지 않는다. 확정 회계 result의 execution_ids만 해당 출처의 확정 원장 참조로 표시한다.

## 1단계 한계

완전한 체결 경로·호가·모든 TP/SL 대안·자유문장 판단의 타당성을 입증하는 도구가 아니다. 현재 없는 값은 해시 참조 또는 UNKNOWN으로 남는다. 거래소 DEMO MAIN 소유권 검증은 이 함수에 없으므로 scope를 UNKNOWN_NOT_ATTESTED로 표시한다. 배포자가 지정한 경로만으로 소유권 입증을 주장하지 않는다. 기록 자체의 원본 누락은 복원하지 않는다.

잘못된 JSON/형식의 행은 해당 행 savepoint를 rollback하고 그 스트림 cursor를 유지한다. 다른 스트림과 이미 보존된 outbox는 계속 처리하며 DEGRADED와 stream/rowid/error_type을 반환한다. 원본 신원·anchor 변조는 이와 달리 전체 fail-closed다. 한도를 넘는 원본 행은 자동 생략하지 않고 해당 스트림 backlog로 남긴다. 기록이 큰 경우나 현재 선택하지 않은 상세 체결값은 원본에서 따로 대조해야 한다.

구현 검증과 운영 연동·배포·최초 자연 실행·ClickStack 수신은 별도 완료 기준이다. 기존 btc 요약 이벤트와 btc.research 이벤트를 합쳐 거래 수나 손익을 중복 합산하면 안 된다.
