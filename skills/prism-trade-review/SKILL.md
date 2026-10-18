---
name: prism-trade-review
description: Review one PRISM-INSIGHT KR/US trade or ticker from the ClickStack event ledger, where one trace_id links screening, buy decision, order fill, every hold/sell review and exit. Use for requests such as "LLY 왜 샀어?", "이 종목 복기해줘", "어제 매도 이유 보여줘", "체결됐어?", "이 trace_id 경과", or any question about what the system decided and why. Read-only. Not for cohort statistics or rule changes (use prism-entry-quality-analysis / prism-trading-change-review).
---

# PRISM Trade Review

Answer "what did the system decide, when, and why" from the event ledger before reaching for
server SQLite, log grep or memory. Read-only: never write to ClickHouse or change trading code here.

## 1. Look it up

```bash
python tools/clickstack_query.py ticker LLY --days 60      # decision traces, newest first
python tools/clickstack_query.py trace <trace_id>          # the whole lifecycle in KST order
python tools/clickstack_query.py trace <trace_id> --full   # untruncated AI rationale and reasons
python tools/clickstack_query.py events --days 7 --prefix entry   # is the event stream alive?
```

The tool reaches ClickHouse on prism-backend through db-server (ssh alias `prism-db`, override
with `PRISM_DB_SSH`). Credentials stay on the backend. For a question the subcommands do not
cover, `sql "SELECT ..."` runs a read-only query; `LogAttributes['event.name']`,
`LogAttributes['prism.ticker']`, `TraceId`, and the full event JSON in `Body`
(`JSONExtractString(Body, 'attributes', ...)`) are the useful columns.

## 2. Read the lifecycle

Events and linkage rules are in `docs/PRISM_OBSERVABILITY.md` ("Trading context ledger",
"Trace linkage"). In a trace, read in this order:

1. `screening.candidate_linked`: which trigger and regime picked it, rank in trigger.
2. `candidate.evaluated` / `entry.executed`: buy score, adjusted score vs minimum, gate findings,
   slot use, regime, and the AI `rationale`.
3. `entry.fill_reconciled`: `SUBMITTED_ONLY`, `REJECTED` or a confirmed fill. A recorded position
   with a rejected order is a simulator position without a real fill; say so.
4. every `holding.evaluated`: price, profit, stop, target and the AI reason for each review.
5. `exit.executed`: sell price, profit and reason.

## 3. Rules

- An event that is not in the ledger is `MISSING`. Trade linkage exists for decisions from
  2026-10-08 (#981, #983); retention is 180 days; backfilled rows carry
  `ingestion_mode=backfill` and have no reconstructed prompts or gates. Fill gaps from
  SQLite or logs only when needed, and name the source.
- Not linked by design: `broker.order_request` (join by ticker, time and
  `execution_profile_ref`) and `job.run_completed` (run level). A live re-entry buy is a new
  trace that carries `origin_trace_id`; follow it back to the stopped-out decision.
- One trade's story is a fact check, not evidence for a rule. Several trades compared for a
  decision go through `prism-entry-quality-analysis` (Evidence Packet) and
  `prism-trading-change-review`; the charter's line 3 (no global rule from one trade) applies.

## 4. Report

Korean, plain investor words, short: what was bought or sold and when, the deciding numbers
(score vs minimum, regime, profit, stop), whether it really filled, and anything `MISSING`.
Paste the timeline in the chat; add the `trace_id` so the user can open it in ClickStack.
