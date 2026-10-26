# M2 공개 호가로 분리한 시장성 체결 오차

2026-10-26. 기존 세 번째 거래의 진입/청산을 그대로 고정한 개발 진단이다. 실제 체결·주문2개와 독립 공개 호가를 비교하며 기존 재현 기준과 비용 가정을 변경하지 않았다.

## 자료와 복원

[Bybit 공식 다운로드](https://www.bybit.com/en/derivative-activity/history-data)의 공개 list-files 응답에서 2026-10-24 BTCUSDT orderbook200 일별 파일을 확인했다. 원 파일161,949,358바이트 중 앞16,252,928바이트만 HTTP Range로 읽었다. 압축 해제95,652,705바이트/69,677개 사건, 최초 snapshot1개와 이후 delta로 두 구간을 복원했다. 전체 파일 CRC 검증은 하지 않았고 prefix SHA256은 `ebd9ea60db0cf86ab066f884ae90137a297ae39b335f232aafc62a0421ff290a`다.

[공식 Orderbook 규약](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook)에 따라 snapshot은 교체, delta의 수량0은 삭제, 나머지는 삽입/갱신했다. 값·시간·update ID·심볼과 교차 호가를 검사하고, 얇은 호가의 VWAP 및 삭제 self-check를 실행했다. 200단계는100ms 주기이며 그 사이 모든 상태를 관측한 것은 아니다. archive의 ts와 cts를 모두 보존했다. cts는 호가의 매칭엔진 시각이며 계좌 체결의 동일성 증거는 아니다.

## 가격·시각 비교

| 역할 | 실제 체결 가격 | 직전 공개 호가에서0.022 BTC 체결 가정 | 차이 | 직전 호가 cts와 체결 시각 차이 |
|---|---:|---:|---:|---:|
| 시장성 지정가 Sell 진입 | 80794.6 | 80795.2 | +0.6 | 11ms |
| Market Buy 종료 | 80921.9 | 80921.9 | 0 | 35ms |

두 주문의 실제 기록 execTime은 거래소 주문 응답 time보다 각각1ms 앞이었다. 로컬 요청/응답 시간으로 시계 오차를0으로 가정하지 않는다. [공식 접수 응답](https://bybit-exchange.github.io/docs/v5/order/create-order#response-parameters)은 비동기 접수 확인이며 체결 완료와 구분한다.

실제 execTime을 호가 선택에 쓰지 않는 추가 진단도 수행했다. 주문 응답의 서버 time 직전 cts로 선택해도 같은 두 가격이다. 로컬 ACK 이후 첫 공개 체결을 쓰던 재생의 지연166ms/1797ms는 첫 공개 호가를 쓰면91ms/66ms로 줄지만, 이것도 완전한 실행 재생이나 실제 체결 지연은 아니다.

응답 시점 호가와 기존0.055% 수수료만으로 계산한 가정 순손익은 -4.74417691 USDT, 실제 -4.75736965와의 차이는 +0.01319274 USDT다. **원 허용오차0.01 USDT를 여전히 넘는다.** 기존5bp 슬리피지/1bp 스프레드 재생의 -1.94480968 USDT 오차와 구분하며, 호가 진단을 그 재생의 통과로 대체하지 않는다. 새 호가 비교의 펀딩을 별도로 재계산하지 않았다.

## 결론과 다음 단계

정확한 일치와 수익성은 미입증이다. 100ms 호가 사이의 상태·대기열·데모 계좌 체결 연결은 여전히 부족하다. 공개 호가와 실제 주문 체결 ID를 임의 연결하거나 두 건을 근거로 전역 비용·지연을 맞추지 않는다. 가격 진단과 실제 모델 검증을 구분하고, 추가 시간순 개발 표본에서 주문 유형·호가 커버리지·시각 오차를 먼저 측정한다.

실제 주문0·추가 모델 호출0. 운영 전략·위험·백테스트 파라미터 변경0. 공개 데이터는 mainnet이며 [데모 공식 문서](https://bybit-exchange.github.io/docs/v5/demo#domain)가 공개 스트림 공유를 설명하지만, 계좌 체결의 개별 일치를 보장하지는 않는다.

근거: `tasks/btc-execution-timing-20261026/orderbook/`의 source.json, 압축 prefix, extract.py, reconstructed.json, price-comparison.json, response-time-comparison.json. 원 등록·실패 결과는 보존했다.
