# 보유 가격관계 설명 계약 v1

M0 설명 경계 수리이다. 수익성 입증이나 새 매매 규칙이 아니다.

## 적용 범위

실제 runtime의 calculated review 보유 판단에만 `explanation_contract_version=1`을 부여한다.
구버전 입력·테이프, single-pass, 무포지션 및 미체결 진입만 있는 상태의 형식은 바꾸지 않는다.
WAIT·후보·최종 SELECT·후보 없는 판단에 같은 구조화 설명 경계를 적용한다.

모델의 rationale/no_candidate_reason은 `interpretation`, `evidence_refs`, `comparisons`로 구성한다.
comparison에는 `level_ref`, `target_ref`만 있으며 가격·출처·관계값은 모델이 쓰지 않는다.
`interpretation`은 원문을 보존하고 표시할 때 `모델 해석(미검증)`으로 구분한다.
thesis_assessment.reason 등 다른 자유문장의 사실성은 여전히 수동 검토 대상이다.

## 출처와 표시 기준

호스트가 동결 입력에서 Mark, 30분·1시간봉의 자체 시점 가격/MA,
4시간·12시간·일봉·주봉 MA만 제한된 참조 사전으로 만든다. 15분봉은 포함하지 않는다.
`P:30m:0:low`는 해당 프레임 point 0의 원래 phase·시각을 유지한다.
상위 MA는 `MA:4h.ma10.forming`, Mark는 `MARK`로 참조한다.

WAIT/REDUCE/EXIT는 `CURRENT:TP:<id>`로 현재 계획을 표시한다.
ADJUST는 `c0:TP:<id>` 또는 `c1:TP:<id>`로 해당 후보 검산 후 가격을 표시한다.
다른 후보의 목표 참조는 UNKNOWN이다. SELECT는 선택 토큰에 연결된 계획만 사용한다.
표시 계획은 실제 주문 상태·체결·지지저항·가격 도달 순서의 증거가 아니다.

방향별 숫자 순서는 BEFORE/AFTER, 같은 가격은 EQUAL이다. 없는 참조·유효하지 않은
출처는 UNKNOWN이며 설명 결측만으로 보호나 FAILED EXIT를 차단하지 않는다.
모델의 원문은 MODEL_RAW 및 원래 wrapper/raw_proposal에 보존한다.
공식 표시 영수증은 원문·원값 사전·표시 계획·대조 결과를 해시로 연결한다.
행동·가격·수량·위험 필드는 변경하지 않는다.

## 제한과 검증

사전 뷰는 기존 입력이 정리된 뒤 100KB 안에 들어갈 때만 추가하며,
생략은 명시한다. 해석 600자·근거 4개·비교 4개를 표시 대상으로 제한하고
기존 2,000자 설명 한도를 넘기지 않는다. 긴 원문을 숨겨 통과로 보지 않는다.
잘못된 자유 해석은 공식 숫자 대조가 맞더라도 오류이며 실제 모델 검증에서 별도로 판정한다.

필수 반례: LONG/SHORT·동가·결측·15분 제외·후보 교차 참조·ADJUST 가격 보정·phase/시각·
입력 및 영수증 변조·100KB·원문 보존·FAILED 보호·구버전 형식.
구현·오프라인 검증·CI·실제 모델 검증·배포·자연 관측은 각각 분리해 보고한다.
