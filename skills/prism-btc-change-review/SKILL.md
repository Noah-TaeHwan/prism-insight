---
name: prism-btc-change-review
description: Review PRISM BTC (prism-btc, Bybit demo) strategy, execution, protection/stop, sizing, backtest/replay, profitability claims, observability or deployment work before implementing or reporting it. Use for requests such as "BTC 손절 바꿔줘", "BTC 수익 개선됐어?", "BTC 백테스트 돌려줘", "BTC 배포해줘", or any change under prism-btc/ or tools/*btc*.
---

# PRISM BTC Change Review

BTC has its own governance. The roadmap is the single source of truth for direction,
priority and status: `docs/BTC_ROADMAP_ko.md`. Development-time only — the roadmap says no
extra resident harness or LLM trading batch is added.

## 1. Locate the work

1. Read `docs/HARNESS_CORE_ko.md`, then the "현재 위치", milestone and "5. 매번 확인하는 개발 규칙"
   sections of `docs/BTC_ROADMAP_ko.md`. Name the milestone/task ID (M0–M6) and its prerequisites.
2. Read the current design `docs/BTC_SYSTEM_BLUEPRINT_20261005_ko.md` for the part you touch,
   and any contract the roadmap links for that milestone.
3. Agent memory and old chats are pointers only, never status evidence.

## 2. Priority and separation

- Unresolved execution/protection defects come before parameter optimization.
- Keep execution-safety fixes separate from strategy experiments (different PRs/receipts).
- Real funds stay off unless the user separately approves. Risk-budget defaults are not
  changed by a review-workflow preference.
- For strategy/sizing/exit changes also apply the fit gate in
  `docs/TRADING_CHANGE_REVIEW_HARNESS.md` (BTC: follow its own signal → execution → holding path;
  do not assume stock screening exists).

## 3. Evidence rules

- Fix the verdict criteria before implementing. Test normal cases and tampering/missing/boundary
  counterexamples. Synthetic case counts are not trade counts.
- Never create samples with test orders or forced closes; open trades end naturally (roadmap §4).
- Profitability: the roadmap M5 minimum gate evaluated with `prism-btc/analysis/profitability_gate.py`.
  A trade count alone is not REVIEW_READY: costs, funding, execution, MTM, holdout, multiple
  testing, paired CI, drawdown and 2× cost expectancy must all pass. If the criteria change after
  seeing results, re-register as a new experiment version; never relax them. REVIEW_READY is not
  execution approval and nothing deploys automatically. For M3–M5 proof work also follow the
  dated contract `docs/BTC_PROOF_GATES_20261010_ko.md`.
- Missing data, failed checks and unassessed items are never merged into success.
  Already-reviewed losses and A/B inputs are development samples, not holdout.
- Use read-only SQLite snapshots and the code manifest of that time for post-deploy evidence.
- More timeframes, a green test suite or a higher backtest return are not proof of live safety
  or profitability.

## 4. Messages

Trade notifications follow `docs/BTC_POSITION_MESSAGE_CONTRACT_20260915_ko.md`. Never
substitute strategy capital for broker figures; unknown stays unknown.

## 5. Close out

Run `prism-btc/tests` (offline) plus targeted tests. Update the roadmap only where evidence
changed, and state separately: implementation / tests / CI / deployment / forward observation /
profitability proof. Name the next task. Report in short Korean.
