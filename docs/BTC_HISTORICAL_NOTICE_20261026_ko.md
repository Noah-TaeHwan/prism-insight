# M0/M3 정상 격리 상태의 반복 경고 수정

사용자가13:15의 historical_unsettled_excluded 경고 반복을 보고했다. 원인은 새 pending·격리 HALT가 없는 정상 격리 분기에서도 level=warn을 반환하고, notifier가 정보와 경고를 구분하지 않고 매번 확인 필요로 발송한 것이다.

수정: 해당 상태를 info로 남긴다. 일반 정기 점검에서는 발송하지 않고 btc_events 감사 이력을 유지한다. --daily에서는 미정산 건수·성과 제외를 안내하며 정산 완료 또는 무조건 정상으로 표현하지 않는다. --no-send 출력도 안내 수준을 유지한다. 다른 경고/장애와 함께 있으면 실제 문제의 warn/alert와 확인 필요를 유지한다. 알 수 없는 level은 조용히 숨기지 않고 기존처럼 warn으로 처리한다.

변경 경로는 _check_execution_stalled → run_healthcheck → notify_health/메시지 렌더러 → 운영 채널이다. 주문·보호·위험·미정산 행·성과 계산을 수정하지 않는다. 과거 UNKNOWN2건은 보존되며 나중 체결/조회 공백으로 발생하는 incident_epoch_halted와 신규 execution_stalled 경보도 유지한다.

수정 전 정보의 반복 경고/잘못된 표시를5개 실패로 재현했다. 정보 단독의 일반/일일 점검, 실제 경고/장애 혼합, 실제 격리2건의 생산 경로를 검사한다. 관련 테스트·정확한 head CI·배포·첫 정기 점검을 따로 기록한다.

검증 결과: 관련160개 통과·4개 skip. 알 수 없는 severity도 경고로 유지하는 반례를 추가했다. 전체 검사는 PR CI에 맡기며 아직 배포 완료를 뜻하지 않는다.
