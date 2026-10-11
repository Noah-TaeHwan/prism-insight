---
paths:
  - "prism-btc/**"
  - "tools/*btc*"
---

# PRISM BTC 코드 (하네스)

- `prism-btc-change-review` 스킬 절차를 따른다. `docs/BTC_ROADMAP_ko.md`에서 마일스톤·과제 ID와 선행 조건을 먼저 확인한다.
- 실행·보호(손절·청산) 결함 수정이 파라미터 최적화보다 먼저다. 안전 수정과 전략 실험은 다른 PR로 나눈다.
- Bybit 데모가 기본이다. 실자금·위험 예산 기본값 변경은 별도 승인 없이는 하지 않는다.
- 수익 개선 주장은 `docs/BTC_PROOF_GATES_20261010_ko.md`와 `prism-btc/analysis/profitability_gate.py`로만 한다.
  테스트 통과·백테스트 수익·시간프레임 추가는 입증이 아니다. 누락·미평가를 성공에 합치지 않는다.
- 끝나면 `prism-btc/tests`를 돌리고, 로드맵은 증거가 바뀐 곳만 구현·테스트·CI·배포·관측·수익 입증을 구분해 갱신한다.
