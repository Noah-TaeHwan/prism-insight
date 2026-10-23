# BTC 신규 진입 위치 명료화

## 변경 적합성
1. M4 경제적 의사결정 지침 명료화다. 기준은 f8380dc1이며 입증된 손실 원인이나 계산 오류의
   수리라고 부르지 않는다. 부분손절·보유·위험·집행 정책은 바꾸지 않는다.
2. 가설은 방향이 맞다는 설명과 현재 제안 가격에서 남은 경로가 타당하다는 판단을 분리하면
   늦은 진입을 작은 수량·가까운 목표로 정당화하는 혼동을 줄일 수 있다는 것이다.
   기존에도 이런 비교가 충분하거나 근거 있는 강한 돌파를 새로 봉쇄하면 개선 가설은 지지되지 않는다.
3. frozen market → DRAFT 제안 → host 계산 → SELECT → fresh guard → 실행 흐름 중
   기존 FLAT_ENTRY_PROMPT 비교 문단 하나만 교체한다. DRAFT는 원래 제안 limit/최악 허용 가격,
   SELECT는 제공된 effective_plan 가격을 기준으로 한다. trigger는 발동 조건이지 가격 이점이 아니다.
   관찰한 반응 구간과 TP까지 남은 경로를 비교하되 MA·마디가격 자체를 자동 장벽으로 만들지 않는다.
4. 보관 20건은 개발 표본이며 손익으로 사례를 선별하지 않는다. 실제 원요청·원제안·계산 receipt를
   고정해 비교한다. 독립 holdout·수익성 표본 요건을 충족했다고 주장하지 않는다.
5. scope/schema/계산/보호 회귀, 고정 모델 검증, 전체 오프라인 검사와 CI를 통과해야 데모 배포한다.
   새로운 실패·숫자 혼동·불필요한 돌파 차단이 나오면 보류한다. 통과 추격 재호출은 하지 않는다.
6. 현재 상태는 사전등록된 공학적 명료화 검증이다. 승률·손익비·수익 개선은 미입증이다.

## 고정 검증
workspace `btc_entry_location_prereg_20261023.md`를 구현 전에 읽고 동결했다.
SELECT는5975519/5974593/5975084/5975560의 기준→후보 각1회, 이후 후보 DRAFT는5975519/5974593
각1회로 총 HTTP 최대10회다. 재시도·주문은0회이며 첫 기술 오류에서 중단한다.
후보 SELECT 네 건은 실제 진입 가격, 첫 근거 있는 반응 구간, TP의 상대 순서와 통과 필요성을
비교해야 한다. 반응 구간이 TP 뒤에 있거나 경로 안에 없거나 근거가 미상인 경우도 유효하며,
존재하지 않는 장벽을 만들지 않는다. 필요한 비교가 빠지면 미관측이다.
특정 문구·WAIT·진입 방향은 강제하지 않는다.
기존에 근거 있는 즉시 돌파/조건부 진입을 추가 종가·재시험·고정 buffer/RR로 막아서는 안 된다.
DRAFT 두 건은 출력 계약·정책 보존을 점검하며, 후보 없음·반대 방향도 허용한다.
중대한 수치·시간봉 phase·권한 오류는 통과가 아니다. 기준 응답도 적절하면 개선폭은 미입증이다.

## 변경하지 않는 것
새 schema·기하 계산·차단 관문·호출·타임아웃을 추가하지 않는다. 부분손절, holding/pending,
고정 거리·최소 손익비·의무 재시험·MA 자동 장벽, 위험·사이징·실행 권한은 변경하지 않는다.
코드 구현·검증·CI·배포·자연 판단·수익성은 각각 별도 상태로 보고한다.

## v1 결과 및 v2 사전 보완
v1(3df49913)은 HOLD다. SELECT06에서 필수 위치 비교가 미관측됐고 DRAFT10에서
확정/형성 가격 인용 오류가 발생했다. 이전 호출·판정은 보존하며 성공으로 소급하지 않는다.
전체 검사에서 기존 시각 fixture의 부동소수점 미래 시각 문제도 발견했다. 기준/후보 모두
동일하게 재현됐으며 운영 freshness는 완화하지 않고 테스트 시각만 별도로 고쳤다.

v2는 `btc_entry_location_prereg_v2_20261023.md`를 구현 전에 읽고 고정했다.
현재 운영 기준7d1cc8c9 대비 같은 FLAT_ENTRY 두 문단만 보완한다.
current_primary_frame_facts는 형성 가격이며 확정 OHLC는 timestamped recent_confirmed_bars
또는 CONFIRMED points에서 읽는다. 확정→형성 변화를 확정봉 진행이라고 부르지 않는다.
eligible OPEN이 있는 SELECT는 WAIT를 선택해도 기존 rationale의 결정 근거에서 유효
진입 가격·첫 TP·반응 구간의 실제 상대 위치를 연결한다. 구간이 없거나 미상이면 그대로
말하며 후보 없음/최초 관측에 계획을 발명하지 않는다. 새 heading/schema/검증 관문은 없다.

v2 실제 진단은 동일20건 원자료를 현재 조립기에 스텁으로 넣어 현재 계산 receipt를 생성한다.
기준/후보 user 입력·schema·receipt가 같고 승인된 FLAT system 문단만 달라야 한다.
이 receipt는 당시 원래 receipt가 아니라 현재 계산 결과다. 사례/순서/최대10 HTTP/무재시도,
중대한 오류와 필수 비교 미관측의 HOLD 기준은 v1보다 완화하지 않는다. 수익 개선은 미입증이다.

## v2 HOLD와 v3 입력 표시 경계
v2(b7c3c3be)는 전체4872 PASS/12 SKIP/CI8 SUCCESS에도 SELECT04의 확정/형성 및 TP/MA
순서 오류로 HOLD다. 원문과 판정을 보존하며, 동일 문구를 반복 호출해 통과시키지 않는다.
v3는 구현 전에 `btc_entry_location_prereg_v3_20261023.md`를 읽고 동결했다.

추가 logical 입력은 flat의 `contract_context.entry_display` 한 필드뿐이다.
- confirmed_close_tail은 기존 검증된 ma_structure.primary의 CONFIRMED point 최대3개에서
  [봉 종료시각ms,close]를 그대로 복사한다. 형성값은 기존 별도 facts에 남고, 누락은 null이다.
- candidate_price_order는 [후보ID,receipt SHA,status,가격순 그룹]이다. 그룹은 [가격,refs]이며
  E#/T#는 해당 effective_plan의 entries/take_profits 인덱스, M:id는 기존 MA level 키다.
  동가는 한 그룹에 묶고 전체 가격순은 상승순이다. 시간·체결 순서·지지저항 강도를 뜻하지 않는다.
- 후보 최대2, entries/TP 각각20, 기존 상위 MA level16개를 넘으면 명시적으로 표시 불가다.
  무TP도 허용하며, 수량·분할비율은 표시 순서나 원래 계산에 영향을 주지 않는다.
  receipt 해시가 다르거나 값이 유효하지 않으면 해당 표시만 UNAVAILABLE이다.

기존 position_flow 축약을 먼저 완료하고 그 최종 입력을 복원한 뒤 표시만 추가한다.
이후 기존 lossless projection/v7만 사용하며 history를 한 번 더 줄이지 않는다.
표시 추가만으로100KB를 넘고 기존 입력은 맞으면 기존 projected 입력과 audit를 그대로
반환한다. entry_display 부재는 표시 미제공이지 사실 없음이 아니며 원자료를 사용한다.
기존 입력 자체가 초과한 경우는 실패를 유지한다. 고정 모델 검증 사례에는 표시가 실제로
존재하는지 호출 전에 확인한다. 최초 개발 검사에서 추가 표시로 history축약이
달라진3건을 확인해 순서를 고쳤다. 후속 작업트리 검사40요청은 새 표시 외 logical차이0,
최대99725B였고2후보 pressure의 기존 초과2건도 그대로다. 최종 head 검증과는 별도 기록이다.

새 helper는 기존 policy source/loaded parity manifest에 포함한다. 보유/대기/계약·receipt·
schema·위험·집행은 바꾸지 않는다. 기존 두 FLAT 문단에는 표시 필드 참조만 연결했다.
새 heading·추가 판단 관문·확률·방향 추천·자동 MA 장벽은 없다. 자유서술 정확성은 여전히
보장되지 않으며 사전등록된 최대10회 진단과 독립 검토를 통과해야 제한적 데모 배포를 검토한다.

## Sol low 운영 전환 검증
사용자가 gpt-6.1-sol/low 운영 전환과 검증된 PR 배포를 승인했다.
구현 전에 `btc_sol_release_prereg_20261023.md`를 읽고 고정했다. 이전 v1/v2/v3 및
high/xhigh/Sol 단독 비교의 실패·미관측·HOLD는 변경하지 않는다.

BTC 판단의 공통 MODEL/EFFORT만 Sol/low로 바꾼다. 초안·선택·보유·예약 판단은 같은
상수를 사용하며, preview와 새 replay metadata도 현행 상수를 참조한다. 과거 저장된
tape·등록·결과는 재작성하지 않는다. OAuth capability는 Sol/low와 명시적 이전 연구용
Luna/high·xhigh 세 조합만 허용하며 교차 조합은 거부한다. 자동 fallback은 없다.
기존 SYSTEM 인용 문단에 시간봉/phase별 수치 변화는 따로 귀속하고 각 원값이 같을 때만
묶어 표현한다는 한 문장을 추가한다. 새 schema·검증 관문은 없다.

고정 실제 연결은 flat5975519, flat5974593, holding5975523, pending5975511 순이다.
과거 DRAFT를 주입하지 않고 각 원자료에서 새 DRAFT→호스트→SELECT를 운영 코드로 연결한다.
원 context.now에 실제 monotonic 경과를 더한 하나의75초 예산을 지키며 HTTP 최대8회,
재시도0·주문0·첫 기술 실패 중단이다. 정상 WAIT/후보 없음은 SELECT를 강제하지 않는다.
flat 최소1건과 active 최소1건의 실제2단계 연결이 필요하며, 미관측 분기는 별도로 남긴다.
출처·가격·시간봉·phase·권한 오류와 유효 OPEN의 위치 비교 누락을 통과로 채우지 않는다.
실제 holding/pending 행동이 없으면 해당 분기의 offline 검증과 구분한다.

75초·크기 한도·계산·위험·부분손절·FAILED 전량 EXIT·명시적 예약 갱신·보호는 유지한다.
모니터링 모델/스케줄과 실자금은 변경하지 않는다. 정확한 head의 관련/전체 검사·CI·서버
격리·독립 검토를 거쳐 Bybit demo만 배포하며 자연 판단과 수익성은 별도 확인한다.
