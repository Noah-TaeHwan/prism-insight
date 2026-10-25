---
name: prism-knowledge-intake
description: Absorb knowledge from a book, article or other outside source the user is reading into PRISM-INSIGHT so that it fits the north star and stays searchable for future sessions. Use when the user shares book pages or photos, quotes an investing author, or says things like "책 읽은 거 올릴게", "인사이트 축적해줘", "이 책 내용 우리 로직에 녹여봐", "예전에 읽은 책에서 뭐라고 했지". Records source summaries and knowledge cards under docs/knowledge/; never changes trading rules directly (use prism-trading-change-review for that).
---

# PRISM Knowledge Intake

Turn outside knowledge into **processed, durable cards** under `docs/knowledge/` (read `docs/knowledge/README_ko.md` first).
Raw ideas from a book must not leak into trading code or prompts unprocessed: an unprocessed idea that
contradicts the north star creates contradictions and noise.

## 0. Look up first
- Read `docs/knowledge/INDEX_ko.md`. If the book or the claim already has a card, update that card
  instead of adding a new one.
- Read `docs/HARNESS_CORE_ko.md` (north star, lines not to cross) and search `docs/RESEARCH_LESSONS_ko.md`
  and `docs/HYPOTHESIS_REGISTRY_ko.md` for the same question. If our data already answered it, cite that.

## 1. Record the source (every time pages arrive)
- Append to the book's SOURCE_ko.md (docs/knowledge/books/<slug>/) under a page-range heading with the reading date.
- This is a public repository: summarize, keep quotes short, never paste long passages.
- Mark anything unreadable or ambiguous (cropped photo, translation doubt) as such. Do not guess.

## 2. Extract claims into cards
For each claim, write or update a card with the template in `README_ko.md`:
source and page, claim, **type** (rule / ideal / case / caution / practice), **scope** (which setup,
regime or stock type it applies to), north-star fit, our system location (file), evidence status, processed conclusion.
- "가장 좋다", "바람직하다", "이상적" are **ideal**, not rules. Never turn an ideal into a hard gate.
- Check which section a rule belongs to before applying it elsewhere (example: the book's 10-day-MA
  extension rule is for continuation pocket pivots only).
- Author examples are hand-picked winners. A claim is not evidence about our market until tested.

## 3. Fit check against the north star (`HARNESS_CORE_ko.md`)
Classify each card:
- **녹일 것 (fits)**: helps big-winner capture, loss/drawdown, stop cost or screening accuracy, and does not
  contradict current rules. Record the exact layer it would touch.
- **검증할 것 (test)**: plausible but unproven in our data. Link or propose a hypothesis ID. Do not implement.
- **충돌 (conflict)**: contradicts a current rule or the north star (example: "sell fast at the 10-day MA" vs
  the runner-hold rule). Write the adaptation: what part fits, what part we reject and why.
- **참고만 / 범위 밖**: keep as reference (leverage, IPOs, concentration outside our 10-slot structure).

## 4. Evidence and status
- Only a preregistered test moves a card to 채택 or 기각. One exploratory split (tens of samples) stays
  검증 중. When a later result overturns an earlier one, **overwrite the card** so only the latest verdict
  remains. Git keeps the history.
- Compare entry quality with ATR-normalized measures, not fixed-% stops.
- Book numbers give direction. Concrete thresholds are chosen per market from our data (user principle 2026-10-22).

## 5. Hand-off
- Any change to screening, entry, exit, sizing or prompts goes through `prism-trading-change-review`. Cards never
  change trading behavior by themselves.
- Update `INDEX_ko.md` (one line per card) in the same change.
- Report to the user in Korean, plain words: what the pages said, which cards changed, and fits / conflicts / tests.
