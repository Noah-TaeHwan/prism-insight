# 취소를 동반한 WAIT의 정상 확인 대기 분류

M0/M2 실행 안정성 보완이며 전략·위험·취소 확인 기준은 변경하지 않는다.

연속2시간 개발 기준선은7HTTP 뒤 WAIT+cancel_entry_ids에서 submission_unknown으로 중단됐다. 원 자료·봉인 장부는 `tasks/btc-continuous-two-hour-20261027/`에 보존했다. 원 소스의 다른 프로세스에서 실패 전체 결과가 정확히 재생됐고, 예외 추적으로 `_cancel`→`_execute`→`execute`의 BrokerCancellationPending(cancel_not_confirmed)을 확인했다. 추가 모델 호출과 실주문은0이다.

broker는 정확한 orderId/orderLinkId로 취소 접수 응답을 받았고 조회 상태가 아직LIVE이면 취소 완료가 아닌 정상 확인 대기로 구분한다. runtime은 그 예외를 EXIT/REDUCE에만 awaiting_cancellation으로 분류해, 취소만 요청한 WAIT도 submission_unknown으로 올렸다. 따라서 재생기가 정상 확인 대기를 기술 오류로 보고 새 판단을 중단했다.

수정 범위는 runtime의 예외 분류 한 곳이다. WAIT이면서 취소ID가 있고 새 진입·예약 갱신이 없을 때만 같은 구체적 예외를 awaiting_cancellation으로 분류한다. 임의 BrokerNotReady, 타임아웃, 누락/불일치 ACK, ADJUST/갱신 등은 기존 오류 처리를 유지한다. executor·broker·위험 계산·확인 기준·재시도 정책은 변경하지 않는다. intent는 PENDING이고 실제 취소 완료·현재 보호 증거 확인 뒤에만 기존 reconciliation이 종료한다.

검증은 실제 runtime/broker/가상 거래소의 single/calculated 두 경로에서 정확 ACK·전송 타임아웃·주문ID 누락을 비교한다. 취소는 각 실행당 한 번, 추가 주문/포지션 생성0, 정상 경로의 최종 intent/child 종료, frozen 전체 결과 일치를 검사한다. 기존 EXIT/ADJUST 및 예약 갱신과 일반 WAIT 회귀도 검사한다. 작은 fixture의 거래 수를 전략 성과로 세지 않는다.

현재 구현과 관련160개 검사는 통과했다(runtime83·새 경계8·EXIT/예약갱신/일반WAIT69). 새 경계8개는 정확ACK·타임아웃·누락ID·다른주문ID를 두 모델 경로에서 확인했다. PR CI·배포·자연 발생 취소 경로·원2시간 전략 검증 완료는 별도 단계다. 원7회 장부는 재개하지 않는다. 재검증의 등록과 호출 예산을 따로 고정하기 전 새 모델 호출을 하지 않는다.
