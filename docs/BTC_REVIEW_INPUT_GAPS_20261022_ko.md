# BTC 계산 후 선택 입력의 중복 MA 간격 참조

M0/M3 입력 초과 장애 수리다. 17:15 slot5975523의 보관 DRAFT(98,737B)를 주문 없이
호스트 계산에 통과시키면 ADJUST/EXIT 두 후보의 SELECT 입력은101,128B로100KB를 넘는다.
완전 동일한 MA 간격 객체 여섯 개가 market_snapshot과 ma_structure에 중복되어 있다.
시장 근거·후보·계좌·위험·입력 상한을 줄이거나 바꾸지 않고 이 중복만 참조로 표현한다.

## 수정 전 검증 계약
- version7은 기존 version1~6 투영 위에만 적용한다. 대상은15m/30m/1h의
  confirmed_gap/forming_gap 여섯 경로뿐이다. 타입 보존 canonical JSON이 정확히
  동일할 때만 공유한다. bool/int/float와 배열·봉 순서는 보존한다. 객체 키의 삽입 순서는
  의미가 아니며 실제 SELECT도 키 정렬로 직렬화한다. 총 바이트가 줄지 않으면 적용하지 않는다.
- 기존 최종 투영이100,000B 이하이면 바이트를 전혀 바꾸지 않는다. 이미 호출 가능했던
  요청의 exact replay를 보존하고 초과 입력만 구제한다. 과거 실패 테이프의 당시 판정은
  당시 코드로 보존하며 새 코드의 오프라인 통과로 성공 이력을 만들지 않는다.
- 기존 선택적 position_flow 축약 순서를 먼저 끝낸다. 그 최종 결과도 초과할 때만
  `allow_gap_rescue=True`로 시도한다. 중간 초과 입력을 먼저 구제해 과거 축약 성공 요청을
  다르게 만드는 일은 금지한다. 기본 projector는 opt-in 없이 version7을 만들지 않는다.
- `$ma_gap: TF.KIND`는 snapshot.timeframes[TF].ma_path[KIND]의 정확한 객체다.
  source와 대상 경로는 고정 목록으로 제한한다. 참조 목록·이전 투영 버전·이전 투영의
  타입 보존 canonical JSON 해시·공통 해석 문구를 metadata에 보존한다.
- 복원은 간격 참조를 해제하고 이전 투영 해시를 검증한 다음 기존 version1~6 복원을
  그대로 사용한다. 등록되지 않은 marker·중복/누락/변조 경로·알 수 없는 버전·원본 변경은 거부한다.
- 원래 입력을 수정하지 않는다. receipt와 raw/effective 계획·해시·exact replay·guard는 유지한다.
  원본 raw `original_sha256` 감사 기록은 바꾸지 않지만 복원 객체의 삽입 순서 해시까지
  같다고 주장하지 않는다. 정확한 복원 기준은 `original_semantic_sha256`이다.
- 실제 보관 DRAFT의 오프라인 SELECT 도달은 실제 모델 선택이나 과거 실행 회복이 아니다.
  모델 검증은 부모 작업의 별도 사전등록된 최대1회 호출만 허용하며 자동 재시도하지 않는다.
- 과거 투영·roundtrip·타입/순서/해시/경로 변조·이득 없는 입력·single/two phase 회귀,
  정확한 head의 전체 검사·CI·독립 리뷰를 거친다. 모니터 복원 소비자도 version7과 맞춘다.
  배포·첫 자연 판단·수익성은 각각 별도 상태다.
