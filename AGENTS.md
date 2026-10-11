# AGENTS.md - Codex Guide for PRISM-INSIGHT

This file governs the repository rooted here.

## Harness charter (read first)

Shared with Claude Code. Repo skills are in `.agents/skills/` (linked to `skills/`), project hooks
in `.codex/hooks.json` (trust them once with `/hooks`).

<!-- HARNESS_CORE:BEGIN (generated from docs/HARNESS_CORE_ko.md; edit that file) -->
# PRISM 하네스 헌장 (Claude·Codex 공통)

사용자와 에이전트가 같은 방향으로 개발하기 위한 최상위 약속이다. 세부 절차는 아래 표의 원문 문서에
있고, 이 헌장은 그 문서들을 언제 읽어야 하는지와 어겨서는 안 되는 선만 담는다. 원문과 충돌하면 더
보수적인 쪽을 따른다. 이 블록은 `AGENTS.md`에 그대로 복사되며, `tests/test_harness_layers.py`가
두 사본이 같은지 검사한다(고칠 때는 이 파일을 고치고 `python tools/harness/sync_agents_core.py` 실행).

## 북극성 — KR·US 주식 (사용자 정의, 2026-10-04)

- 오닐식 추세추종이다. 손익비를 관리하면서 **크게 가는 소수 종목**으로 계단식 성장을 한다.
- 초분할·재진입은 낙폭을 관리하면서 가는 종목을 놓치지 않고 올라타기 위한 수단이다.
- 핵심은 스크리닝과 좋은 종목을 사는 눈의 정확도다. 손절만 반복되는 빈도는 계좌를 녹인다.
- 단타 기준은 쓰지 않는다. 털린 종목은 다시 강해지면 재진입해 큰 흐름을 탄다. 다음 개선의 중심은 재진입이다(2026-10-07).
- 모든 변경·검증·보고는 큰 수익 종목 포착과 비중 확대, 손실·낙폭, 손절 비용, 스크리닝 정확도로 판단한다.
- BTC의 목표는 따로 있다: 실행·측정을 믿을 수 있고 비용 차감 후 개선을 입증하는 시스템(`docs/BTC_ROADMAP_ko.md` 1절).

## 함께 일하는 방식

- 방향이 갈리는 결정(가설 정의·대상 종목·시간축·성공 기준·적용 방식, 완화 vs 강화)은 실행 전에 사용자에게 묻는다.
- "비교해봐"는 지금 몇 건으로 하는 일회성 비교다. 비교 요청을 SHADOW·운영 코드로 넓히려면 먼저 한 줄로 확인받는다.
- 전략 실험은 `tasks/lessons.md`의 실험 설계 교훈을 따른다: 재현 도구가 실제 성과를 맞추는지 먼저 확인, 최소 검출 효과를
  표본으로 사전 계산, 후보를 넓게 비교, 과거 재현 → 소액 실전 2단계로 판정. 성과는 고정 7·14·30일 종가가 아니라 최고·최저 수익률도 본다.
- 매매 규칙은 KR·US를 같은 방향으로 바꾼다. 한쪽에서 정한 결정을 다른 쪽에서 다시 묻지 않는다.
- 사용자 보고는 한국어, 쉬운 투자자 용어로 짧게(결론·핵심 숫자·다음 행동) 한다. 내부 약어는 풀어 쓴다.

## 넘지 않는 선

1. SHADOW·LIVE를 자동으로 켜지 않는다. LIVE는 사용자의 명시적 승인이 필요하다. BTC는 Bybit 데모이며 실자금은 별도 승인이다.
2. `MISSING`(모름)을 0·실패·통과로 채우지 않는다. 판단 당시 없던 값으로 과거를 복원해 일치했다고 말하지 않는다.
3. 한 거래·한 트리거의 결과를 반례 검증 없이 전역 차단 규칙이나 프롬프트 벌점으로 올리지 않는다.
4. 한 번에 한 결정 계층만 바꾼다. 같은 근거를 스크리닝·게이트·프롬프트에 중복으로 넣지 않는다(각 층에 측정된 별도 역할이 있을 때만 예외).
5. 운영에서 LLM이 승인하는 규칙을 결정론 재생만으로 채택·기각하지 않는다.
6. 뉴스·테마는 참고 자료다. 매수·보유·매도 규칙의 근거로 쓰지 않는다.
7. 매매 프롬프트를 바꾸면 형제 프롬프트(BUY·SELL·재진입 × KR·US × ko·en)와 실제 코드를 대조한다. 방향이 갈리는 모순은 사용자와 정한다.
8. 테스트 통과는 운영 검증이 아니다. 배포 확인과 첫 정기 실행 확인을 따로 보고한다.

## 상황별로 먼저 읽을 문서

| 상황 | 먼저 읽기 | 스킬·도구 |
|---|---|---|
| KR·US 스크리닝·국면·진입·청산·비중 변경, 새 전략·트리거 | `docs/TRADING_CHANGE_REVIEW_HARNESS.md`(전략 도입 검토·최소 검토 기록 6항목), `docs/RESEARCH_LESSONS_ko.md` | `prism-trading-change-review` |
| BUY·SELL·보유·재진입 프롬프트 변경 | 위 하네스의 "Prompt framing and logical-consistency review" 절 | `tests/test_sell_prompt_consistency.py` 방식 |
| 매수품질·트리거 비교·재생·승격 검토 | `docs/ENTRY_QUALITY_EVOLUTION_ko.md`, `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` | `prism-entry-quality-analysis` |
| BTC 전략·실행·백테스트·위험·배포 | `docs/BTC_ROADMAP_ko.md`(마일스톤 ID, 5절 개발 규칙, M5 최소 게이트) | `prism-btc-change-review` |
| 뉴스·테마 작업 | `docs/THEME_NEWS_ROADMAP_ko.md` | |
| 배포·장애 수리 | `docs/SERVER_GIT_OPERATIONS_ko.md`의 변경 범위별 검증 게이트 | |
| 2주 정기 점검 | `docs/TWO_WEEK_REVIEW_ko.md`, 하네스의 프롬프트 주기 점검 | `tools/two_week_review.py` |

## 이 헌장이 들어오는 길

`CLAUDE.md`는 `@`로 가져오고 `AGENTS.md`는 사본을 둔다. 매매·BTC·분석 관련 질문이나 매매·BTC 파일 수정 때는
훅이 요약을 다시 넣는다(`tools/harness/hook.py`, 막지 않음). 스킬은 `skills/`에 있고 `.claude/skills/`와
`.agents/skills/`가 같은 폴더를 가리킨다. 규칙 내용은 원문 문서에서만 고치고, 이 헌장을 고치면
`tools/harness/sync_agents_core.py`를 실행한다. 감지 단어·매매 파일 목록은 `hook.py`에서 조정한다.
<!-- HARNESS_CORE:END -->

## Project Summary

PRISM-INSIGHT is an AI-powered Korean/US stock analysis and automated trading system built around:

- Python 3.10+
- GPT-5 / Claude based analysis agents
- SQLite storage
- Telegram delivery
- KIS trading APIs
- KR and US market flows

Primary source material for project context lives in `CLAUDE.md` and supporting docs under `docs/`.

## Repository Map

- `cores/`: main analysis engine, report generation, agent definitions, ChatGPT OAuth proxy
- `cores/agents/`: specialized analysis, communication, and trading agents
- `trading/`: Korean trading integration and account handling
- `prism-us/`: US market mirror flows and trading support
- `tracking/`: journal, memory, trading state helpers
- `messaging/`: Redis and GCP Pub/Sub messaging
- `tests/`: targeted regression tests
- `docs/`: setup, troubleshooting, and agent references

## Preferred Commands

Use targeted, low-side-effect commands first.

### Setup

```bash
pip install -r requirements.txt
python3 -m playwright install chromium
```

### Local analysis runs

```bash
python stock_analysis_orchestrator.py --mode morning --no-telegram
python prism-us/us_stock_analysis_orchestrator.py --mode morning
python demo.py 005930
python demo.py AAPL --market us
python weekly_insight_report.py --dry-run
```

### Focused tests

```bash
pytest tests/test_trading_journal.py
pytest tests/test_publish_guard.py
pytest tests/test_publish_market_propagation.py
pytest tests/test_multi_account_domestic.py
```

Avoid broad production-like runs unless the task requires them.

## Change Rules

- Default to safe paths: prefer `--no-telegram`, `--dry-run`, demo mode, or isolated tests.
- Do not change or commit real credentials, tokens, or secrets in `.env`, `mcp_agent.secrets.yaml`, or `trading/config/kis_devlp.yaml`.
- Treat generated logs, PDFs, JSON outputs, and SQLite databases as user data unless the task explicitly targets them.
- Keep changes narrow and consistent with existing patterns; this repo has substantial behavior encoded in prompts and orchestration order.

## Authorized PR merge and deployment workflow

- User instruction (2026-09-06): for PRISM-INSIGHT task-scoped, validated changes,
  proceed with merge and deployment without waiting for a separate approval review.
- The main branch requires zero approving reviews by explicit user request.
  Preserve the PR requirement, CI/status checks, and all other branch protections.
- Check the exact PR head, dependency order, relevant tests, and CI before merging.
- Any feature change, incident repair, or deployment request must first apply the
  change-scope verification gates in docs/SERVER_GIT_OPERATIONS_ko.md. This is
  mandatory task routing, not optional memory recall. Record pre-deploy tests,
  post-deploy smoke, and whether the first scheduled run has actually completed.
  Do not interpret the review waiver as permission to merge failing or unrelated work.
- Follow docs/SERVER_GIT_OPERATIONS_ko.md: clean target, verified commit, ff-only
  deployment, no destructive reset/stash/clean, and no credential/runtime-data edits.
- BTC deployment remains Bybit demo unless the user separately approves real funds.
  New risk-budget defaults, strategy promotion, and unrelated app-server deployments
  are not authorized merely by this review-workflow preference.

## Engineering Rules

### Async and I/O

- In async flows, use non-blocking patterns.
- Do not introduce blocking network calls such as `requests.get(...)` inside async execution paths; use the repo's async approach instead.

### Agent execution

- Preserve sequential execution of analysis agents unless there is clear existing infrastructure for safe parallelism.
- Do not replace sequential report generation with `asyncio.gather(...)` for LLM-heavy sections; rate limits and prompt ordering matter here.
- Market analysis may use cache-aware behavior; preserve that pattern when editing orchestration.

### Trading and data safety

- Judge and report every trading change by the north star in the harness charter above.
- Default trading behavior should remain safe (`demo` unless explicitly required otherwise).
- Preserve portfolio constraints and stop-loss logic unless the task explicitly changes trading rules.
- When parsing KIS API numeric fields, prefer existing safe conversion helpers over direct casts.
- Before changing screening, regime, entry, exit, or sizing behavior, follow
  `docs/TRADING_CHANGE_REVIEW_HARNESS.md`. In particular, do not generalize a
  trigger-local failure into a global hard gate without testing counterexamples.
- When a request proposes introducing, combining, replacing, or retuning a
  strategy, trigger, factor, regime switch, exit, or sizing rule (including an
  LLM prompt that changes economic decisions), apply the Strategy adoption and
  fit gate in that harness before implementation. Natural-language proposals
  count; no special command is required. Compatibility review must trace actual
  screening code through report inputs, BUY/SELL prompts, deterministic gates,
  execution and exit management; conceptual strategy fit or prompt-only review
  is insufficient. Record intended stage differences versus contradictions,
  source locations and same-candidate tests before claiming compatibility.
  Keep execution/data bug repair separate
  and do not delay a proven safety fix under the guise of strategy research.
- For every BUY/SELL/holding-review prompt change, apply the harness's prompt framing and
  logical-consistency review: trace the assembled prompt like a program, list contradictions
  with existing rules, and ask the user to decide any contradiction whose fix changes direction.

### BTC roadmap governance

- Before BTC strategy, execution, backtest, risk, observability, or deployment work, follow
  `skills/prism-btc-change-review/SKILL.md` (roadmap milestone ID, safety before optimization,
  evidence gates, status updates only with verified evidence; development-time only).

### News/theme roadmap governance

- Before work on KIS news headlines, the headline stores, theme maps, the signal-alert
  theme brief, `/theme`·`/signal` evidence, or research that uses news/theme data (KR or US),
  read `docs/THEME_NEWS_ROADMAP_ko.md`, identify the step ID (R*/U*) and its prerequisites,
  and follow the listed order unless the user changes it.
- Update its status table only with verified evidence (implementation, tests, deployment,
  first scheduled run, and outcome proof are separate states).

### Entry-quality analysis

- Route requests such as “매수품질 데이터 분석해줘”, entry-quality validation,
  trigger comparison/replacement, replay, or entry-rule promotion review through
  `skills/prism-entry-quality-analysis/SKILL.md`.
- Read `docs/ENTRY_QUALITY_EVOLUTION_ko.md` and
  `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` before interpreting the data.
- Generate a deterministic Evidence Packet with
  `tools/build_entry_quality_evidence_packet.py`; do not substitute ad-hoc SQL,
  fuzzy joins, or reconstructed fills.
- Keep `MISSING` as unknown. Strategy-ledger entries/exits are independent of broker
  funding and fills; do not exclude valid strategy outcomes because a broker order
  was rejected. Broker-realized PnL requires separate confirmed entry/exit evidence.
- Never promote a trigger or entry-quality rule to SHADOW or LIVE automatically.
  LIVE requires the trading change harness and explicit user approval.

### Report output

- BTC trade notifications must follow `docs/BTC_POSITION_MESSAGE_CONTRACT_20260915_ko.md`:
  preserve detailed entry/add/reduction/exit/recovery context, verified margin mode,
  exchange leverage, position margin and same-account equity ratio with currency
  and capture time. Never substitute strategy capital/exposure for broker figures,
  attach another position's snapshot to an old exit, or delay protection/execution
  for optional notification enrichment. Missing data stays explicitly unknown.
  Public notices must be compact and event-specific: do not append raw account
  snapshots or repeated unknown fields to exits. Keep full diagnostic data, group
  essential missing-data warnings, and never hide unconfirmed settlement status.
- Korean report text must use formal polite style.
- Preserve existing prompt and report structure unless the task explicitly requests prompt/report redesign.

## File-Specific Notes

- `cores/report_generation.py`: common report tone and section formatting rules
- `cores/analysis.py`: sequential orchestration and section integration
- `cores/agents/*.py`: prompt logic and agent responsibilities
- `stock_tracking_agent.py`: trading loop, sell decisions, optional journal flow
- `telegram_ai_bot.py`: user consultation flows and conversation context

## Before Finishing

- Run the smallest relevant test or command that validates the change.
- For screening/provider changes, unit tests or AST-extracted tests alone are insufficient:
  run real-module KR consumer and US morning/afternoon batch-to-JSON integration
  tests with realistic flat/MultiIndex/empty/ambiguous provider fixtures. Keep
  network, broker orders and channel sends disabled in integration tests. After
  deployment, verify the production Python and a bounded read-only provider
  smoke before claiming operational recovery; do not rerun live orders as a test.
- If you could not run validation, say so explicitly and explain why.
- In summaries, reference the files changed and note any operational risk, especially around trading, messaging, or credential handling.
