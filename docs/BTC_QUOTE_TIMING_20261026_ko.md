# M2 호가 조회 시각 근거 보존

범위: 측정 결측 보완. 전략·주문·비용 모델·위험은 변경하지 않는다. 기존 capture_account의 get_tickers 응답에서 호가 목록만 꺼내고 공통 time 필드를 버려, 저장된 bid/ask와 계좌 관측 시각만으로는 조회 구간·거래소 시각을 구분할 수 없었다.

기존 한 번의 조회 앞뒤 로컬 시각과 응답의 time(ms)을 같은 account 증거에 ticker_timing으로 보존한다. 추가 조회·주문·상주 수집기를 만들지 않는다. 유효하지 않거나 없는 거래소 시각은 null로 남기며 거래 판단을 차단하거나0으로 채우지 않는다. 기존 account_version·시세 신선도 판단·모델 context·가격/수량 계산에는 사용하지 않는다.

[Bybit 공통 응답](https://bybit-exchange.github.io/docs/v5/guide#common-response-parameters)의 time은 현재 타임스탬프이며, 호가 갱신/매칭엔진 체결 시각이라고 설명하지 않는다. [Ticker](https://bybit-exchange.github.io/docs/v5/market/tickers)는 최우선 bid/ask와 수량을 제공한다. [주문 접수 응답](https://bybit-exchange.github.io/docs/v5/order/create-order#response-parameters)은 비동기 접수 확인이므로 체결 완료와 구분한다.

검증 기준: 실제 capture_account→기존 account 증거 저장에서 세 시각 연결, 조회1회, 결측/비정상 응답 시각의 null 보존, 계좌 경제적 버전·실행 권한 불변, 기존10초 신선도 거절 유지. 배포 후 자연 정기 관측의 새 필드 존재를 확인하며 과거 자료를 소급 채우지 않는다. 이 측정 개선만으로 재현 PARITY_PASS나 수익성 입증을 주장하지 않는다.

구현 검증: 변경 전 새 증거 필드 부재를9개 실패로 확인했다. 수정 후 관련203개와 모델 context/실행 권한 불변 통합1개를 통과했다. 전체 회귀·CI·배포·자연 관측은 별도다. 관측 시각이 있다고 체결 당시 호가·대기열·시계 오차를 모두 안다고 주장하지 않는다.
