# BTC 구조화된 원래 목표와 계획 정합성: 구현 전 계약

1. **변경 유형·기준선:** M0/M3/M4의 신규 opt-in 목표 프로토콜이다. 기준선
   `57e3c52b247f09b7885e8b08c2b157ab7d6ca6cc`의 설명 지침만으로는 실제 모델의
   원래 목표/TP 모순 인지가 입증되지 않았다. PR1063은 아직 Draft/미배포다.
   이 버전은 기술적 출발점일 뿐이며 v1 보존 기준은 실제 배포된 `9d4a1466`이다.
   미배포 공통 MA/DRAFT/SELECT 추가 문장은 제거하고 신규 목표/설명 검토 지침은
   v2 전용으로 한정한다. 운영 v1의 공통 프롬프트 문자열·스키마·등록 해시는 보존한다.
2. **작동 이유·반증:** 신규 v2 OPEN은 자유문 expected_progress 대신 goal을
   선택한다. BOUNDED_BEFORE / REFERENCE_EXTENSION / NO_MA_BOUND와 참조 frame,
   phase, MA만 모델이 정한다. 값·시각·입력 해시·공식 목표 문장은 host가 고정한다.
   자유문 전체의 의미 검증은 보장하지 않는다. 구조화 목표와 실제 TP가 어긋나는데
   선택/실행 가능하면 실패다.
3. **기존 경로와 경계:** v1/legacy의 스키마·해시·기존 보유는 소급 변경하지 않는다.
   raw 및 가격 보정 후 effective 계획을 모두 검사하고 원래 목표를 계약에 바인딩한다.
   BOUNDED는 모든 entry/TP가 LONG이면 원래 bound 미만, SHORT이면 초과여야 한다.
   후속 ADJUST도 원래 bound를 유지하며 EXIT/손절 강화/부분손절/취소를 방해하지 않는다.
   도달만으로 FAILED/EXIT하지 않는다. 위험·수량·만료·예약 lease는 기존 정책이다.
4. **사전 검증:** 대칭 방향, equality/근소 초과, raw 위반 후 보정으로 안쪽 이동,
   raw 정상 후 보정으로 초과, 여러 TP, 참조 결측/미래/변조, 확정/형성, legacy,
   부분체결·보호-only, 재시작·tape/hash·pending lease 불변을 검증한다. 실패 후보만
   typed PREAUTHORITY_PLAN_CONSISTENCY로 제외하며 일반 스키마/권한 오류는 숨기지 않는다.
   실제 최대 요청 100KB를 유지하고 필수 근거 삭제·대형 catalog는 추가하지 않는다.
5. **원인 분리·복귀:** 구조화 일치 검사, 모델 목표 선택의 적절성, 자유문 설명,
   실현 성과를 구분한다. 오프라인 성공을 모델 능력·수익성 증거로 해석하지 않는다.
   기존 계약 손상/보호 방해/허용 범위 확대는 배포 차단이다. 부모의 한정된 실제 진단과
   독립 검토 이후 전체 테스트·CI·배포·자연 관측을 각각 기록한다.
6. **현재 결론:** 사용자 승인하에 구현한다. 모델 reasoning effort는 high 그대로다.
   추가 모델 호출·운영 주문·스케줄 변경은 하지 않는다. REFERENCE_EXTENSION의
   신규 OPEN에는 기존 step/minimum 수량 기준으로 실행 가능한 너머 TP 또는
   의도된 미고정 잔여가 있어야 한다. 전량 앞쪽 TP의 절삭 잔여는 runner가 아니다.
   이후 보호-only ADJUST와 EXIT에 이 신규 진입 근거를 다시 요구하지 않는다.
   v2 계약 생성 후에는 v1-only 코드로 단순 되돌리지 않는다. 신규 발급만 끄고
   기존 v2 호환 코드로 보호·정산을 마친 뒤 flat을 확인하여 복귀한다.
   high의 실제 판단에서 목표 mode/자유문 불일치가 남아 사용자 승인된 같은 모델의
   xhigh 비교를 위해 OAuth adapter만 high/xhigh를 허용한다. 운영 EFFORT=high는
   변경하지 않으며 다른 모델·등급·도구·75초 제한은 그대로다.

## 구현 경계

- `scenario_entry_goal.py`: 작은 wire 스키마, frozen 참조 해석, canonical 목표,
  typed 일관성 거절. 기존 숫자/호스트 계산을 재사용한다.
- entry contract/contract/preview/LLM: v2 게이트와 원래 계약 바인딩, v1 분기 보존.
- calculated review/guard/runtime/executor: raw·effective·commit·실행 시 동일 원래 목표.
- projection/replay/notice: 손실 없는 복원·기존 해시·보유/잔여 알림 보존.
- 테스트: 위 반례와 전체 오프라인 suite. 부모는 실제 모델·서버·배포를 담당한다.

## 입력 예산 보완

부모의 최대 보존 DRAFT 재조립에서 v2 입력이 99,989B로 한도에 접근했다.
승인된 최소 보완은 v2 user payload의 response_contract 중복 schema만 공유하는
lossless projection v6이다. API schema와 시장·계좌·receipt는 바꾸지 않으며,
원래 schema의 삽입 순서·숫자 타입·SHA를 복원한 후 기존 projection을 복원한다.
이득이 없으면 기존 표현을 유지한다. v1 wire는 기존 분기를 그대로 유지한다.
