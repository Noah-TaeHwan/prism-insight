# M2/M3 판단 문맥 의미 교정

## 승인 범위와 가설
2026-10-16 사용자가 승인한 반등 실패·눌림 방어의 방향성 및 현재 권한 오독 교정이다.
MA10/35, 30분·1시간 우선, 상위 프레임 장애물, 15분 최소 입력을 유지한다.
거래 횟수 증가나 수익 개선을 입증한 변경은 아니다. 정상 반등·횡보·단순 접촉은 반례다.

## 실제 조립 경로와 충돌 검토
`scenario_calculated_review.research_review`는 SYSTEM → MA_STRUCTURE → flat일 때
FLAT_ENTRY → DRAFT/SELECT를 조립한다. MA_STRUCTURE에만 양방향 시간 순서를 추가한다.
반등 후 저항 실패·MA10 재이탈은 SHORT 후보, 눌림 후 지지 방어·MA10 재회복은 LONG
후보다. 추가 재시험·확정봉·이평 배열 교차를 일률 강제하지 않지만 자동 진입도 아니다.
own-time ma_path/points만 시간 순서의 근거이며 OHLC 내 고저 순서는 알 수 없다.
기존 no mandatory retest, higher-frame obstacle, 비용·위험 조건과 충돌하지 않는다.
보유 문맥은 반대 포지션을 새로 만드는 권한을 받지 않으며 ADJUST/EXIT 규약을 유지한다.

`_model_payload`의 복사본에 current_decision_state를 추가한다. 현재 bool 플래그와
기존 response_schema의 wire action 선택지를 표시하며 주문 권한을 부여하지 않는다.
baseline_kind는 과거 기준점의 기원이다. first_observation=False만으로 진입 허가를
만들지 않는다. 누락·비bool은 null이고, 실제 recovery/fresh guard는 그대로다.
SELECT에서 eligible=true와 실제 enum 토큰의 선택 가능성을 설명한다. 영수증의
executable=false는 자체 주문 권한 부재이지 부적격 후보가 아니다. eligible=false는
계속 enum에서 제외한다. 원본 문맥·계산 영수증·해시·토큰은 수정하지 않는다.

## 검증과 남은 위험
신규 회귀 6건은 기존 코드에서 실패한 뒤 수정 후 통과했다. 현재 상태·누락·비bool,
LONG/SHORT 보유, 원본 불변, 모델 입력 증가량, 기존 first WAIT 및 거절·정확 재생을 검사한다.
프롬프트 문자열 검증은 모델의 실제 이해 보장이 아니다. 정규 실행의 실제 입력·응답에서
현재 상태 해석과 후보 선택 근거를 별도로 확인해야 한다. 배포·정규 관측·수익 개선은
이 문서 작성 시 미확인이다. 위험·레버리지·모델·주기·보호 공식과 API 호출 수는 유지한다.
