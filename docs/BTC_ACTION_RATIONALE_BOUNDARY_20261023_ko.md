# BTC 선택 행동과 설명의 경계 수리

## 확인한 원인
M0/M3 설명 전달 경계 수리다. 실제 slot5975650의 DRAFT 후보c0는 EXIT이고 근거도
전량 종료 대안이었다. SELECT는 c0 receipt 토큰을 선택하면서 자유설명에는 기존 주문을
유지한다고 썼다. guard_review가 원후보 rationale를 SELECT 자유설명으로 덮어써 최종
EXIT 실행계획에도 유지 설명이 들어갔다. 선택 토큰과 문장 중 어느 것이 모델의 진짜
의도였는지 추측하지 않는다. 구조화된 선택 토큰의 기존 행동 권한은 그대로 유지한다.

## 최소 수리
guard_review와 runtime의 사전 proposal 저장 경계에서 rationale 덮어쓰기를 제거한다.
검증된 선택 후보의 원문을 실행계획 근거로
보존하며 SELECT의 비교 설명은 기존 RESEARCH_RESULT.selection 및 원테이프에 별도로 남는다.
여기서 검증은 후보/receipt/행동 연결 검증이지 DRAFT 문장 전체의 사실 정확성 보증이 아니다.
잘못된 SELECT 설명도 삭제하거나 정정된 과거 기록으로 바꾸지 않는다.

새 schema·프롬프트·거절 조건·regex·모델 호출은 없다. 호스트 행동은 기존 action 필드로
표시한다. payload에 문구를 덧붙이지 않으므로 길이 제한이나 번역 실패로 EXIT를 막지 않는다.
WAIT/OPEN/ADJUST/EXIT·FAILED·예약 갱신·위험·가격·수량·주문 권한은 변경하지 않는다.
후보가 없는 WAIT에서도 wait_proposal의 고유 근거를 보존하고 no_candidate_reason은
기존 감사 결과에 남긴다. 단일 호출 경로의 원근거도 보존한다.

## 검증
상반된 선택 설명과 네 행동, FAILED EXIT, 후보 없는 WAIT를 변경 전 실패로 재현한다.
수정 후 선택 원문·원테이프 불변, action 및 나머지 payload 동일, 기존 해시 변조 거부와
명시적 예약 갱신 회귀를 확인한다. 최종 decision.proposal·intent.payload·broker 전달값을
함께 확인한다. 실제5975650은 보존된 DRAFT/SELECT와 clock 순서를 오프라인 재생해
입력 코드 시점 차이를 명시하고 새 모델 판단이나 과거 체결 효과로 해석하지 않는다.
구현·관련/전체 검사·CI·배포·자연 관측은 각각 분리한다. 수익 개선 주장은 없다.
