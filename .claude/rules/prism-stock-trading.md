---
paths:
  - "trigger_batch.py"
  - "prism-us/us_trigger_batch.py"
  - "cores/agents/trading_agents.py"
  - "prism-us/cores/agents/trading_agents.py"
  - "stock_tracking_agent.py"
  - "stock_tracking_enhanced_agent.py"
  - "prism-us/us_stock_tracking_agent.py"
  - "prism-us/us_pending_order_batch.py"
  - "reentry_cooldown.py"
  - "cores/buy_gate.py"
  - "cores/regime_policy.py"
  - "cores/oneil_fallback.py"
  - "tools/trend_exit_seller.py"
  - "tools/hardstop_seller.py"
  - "tools/loop_b_trend_exit.py"
  - "tools/run_reentry_v3_entry.py"
  - "trading/**"
  - "prism-us/trading/**"
  - "prism_core/add_plan*.py"
  - "prism_core/micro_split*.py"
  - "prism_core/reentry*.py"
  - "prism_core/stopout_reentry.py"
  - "prism_core/pivot_reentry.py"
  - "prism_core/runner_hold*.py"
  - "prism_core/sell_regime_context.py"
  - "prism_core/entry_score_policy.py"
  - "prism_core/order_budget.py"
  - "prism_core/slot_weight.py"
  - "prism_core/sector_cap.py"
  - "prism_core/sector_trading_criteria.py"
  - "prism_core/trigger_quality.py"
  - "prism_core/screening_quality.py"
  - "prism_core/negative_equity_f2.py"
  - "prism_core/oneil_*.py"
---

# PRISM KR·US 매매 코드 (하네스)

이 파일들은 스크리닝·진입·청산·비중·재진입 결정을 바꾼다. 수정 전:

- 순수 구현 결함인지, 경제적 결정 변경인지 먼저 구분한다. 결정 변경이면 `prism-trading-change-review` 스킬
  절차(원문 `docs/TRADING_CHANGE_REVIEW_HARNESS.md`)를 따르고 최소 검토 기록 6항목을 남긴다.
- 스크리닝 → BUY → 결정론 게이트 → 주문·비중 → SELL·보유 관리까지 같은 후보를 실제 코드로 추적한다.
  한 함수나 프롬프트만 보고 궁합을 단정하지 않는다.
- 한 번에 한 결정 계층만 바꾸고, 같은 근거를 여러 단계에 중복 반영하지 않는다. KR·US는 같은 방향으로.
- 거절돼야 할 반례 1개와 유지돼야 할 정상 사례 1개를 테스트로 남기고, 무관한 트리거가 그대로인지 확인한다.
- 매매 프롬프트(`trading_agents.py`, `add_plan_prompts.py`)를 고치면 하네스의 프롬프트 모순 점검 1~11번을 적용하고,
  방향이 갈리는 모순은 사용자와 정한다.
- SHADOW·LIVE 활성화는 사용자 승인 뒤에만. 배포 확인과 첫 정기 실행 확인은 따로 보고한다.
